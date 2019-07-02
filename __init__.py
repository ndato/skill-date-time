# Copyright 2017, Mycroft AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import re
import pytz
import time
import tzlocal
from astral import Astral
import holidays

# for location handler
import os, sys
from collections import defaultdict
from urllib.request   import urlretrieve
from urllib.parse import urljoin
from zipfile  import ZipFile

from adapt.intent import IntentBuilder
import mycroft.audio
# from mycroft.util.format import nice_time
from mycroft.util.format import pronounce_number, nice_date, nice_time
from mycroft.util.lang.format_de import nice_time_de, pronounce_ordinal_de
from mycroft.messagebus.message import Message
from mycroft import MycroftSkill, intent_handler, intent_file_handler
from mycroft.util.parse import extract_datetime, fuzzy_match, extract_number, normalize
from mycroft.util.time import now_utc, default_timezone, to_local
from mycroft.skills.core import resting_screen_handler


class TimeSkill(MycroftSkill):

    def __init__(self):
        super(TimeSkill, self).__init__("TimeSkill")
        self.astral = Astral()
        self.displayed_time = None
        self.display_tz = None 
        self.answering_query = False

        # for location handler
        self.geonames_countryname = 'countryInfo'
        self.geonames_city2tzname = 'cities15000'
        self.geonames_url = 'http://download.geonames.org/export/dump/'
        self.country_list = self.get_country_list(self.geonames_countryname, self.geonames_url)
        self.city2tz_list = self.get_locationtz_list(self.geonames_city2tzname, self.geonames_url)

    def initialize(self):
        # Start a callback that repeats every 10 seconds
        # TODO: Add mechanism to only start timer when UI setting
        #       is checked, but this requires a notifier for settings
        #       updates from the web.
        now = datetime.datetime.now()
        callback_time = (datetime.datetime(now.year, now.month, now.day,
                                           now.hour, now.minute) +
                         datetime.timedelta(seconds=60))
        self.schedule_repeating_event(self.update_display, callback_time, 10)

    # TODO:19.08 Moved to MycroftSkill
    @property
    def platform(self):
        """ Get the platform identifier string

        Returns:
            str: Platform identifier, such as "mycroft_mark_1",
                 "mycroft_picroft", "mycroft_mark_2".  None for nonstandard.
        """
        if self.config_core and self.config_core.get("enclosure"):
            return self.config_core["enclosure"].get("platform")
        else:
            return None

    @resting_screen_handler('Time and Date')
    def handle_idle(self, message):
        self.gui.clear()
        self.log.info('Activating Time/Date resting page')
        self.gui['time_string'] = self.get_display_current_time()
        self.gui['ampm_string'] = ''
        self.gui['date_string'] = self.get_display_date()
        self.gui['weekday_string'] = self.get_weekday()
        self.gui['month_string'] = self.get_month_date()
        self.gui['year_string'] = self.get_year()
        self.gui.show_page('idle.qml')

    @property
    def use_24hour(self):
        return self.config_core.get('time_format') == 'full'

    def get_timezone(self, locale):
        self.log.info('get_timezone: Initial Locale: ' + str(locale))       
        try:
            # This handles codes like "America/Los_Angeles"
            self.log.info('get_timezone: Final Timezone from PyTZ: ' + str(pytz.timezone(locale)))
            return (pytz.timezone(locale), locale)
        except:
            pass

        try:
            # This handles common city names, like "Dallas" or "Paris"
            self.log.info('get_timezone: Final Timezone from Astral: ' + str(self.astral[locale].timezone))
            return (pytz.timezone(self.astral[locale].timezone), locale)
        except:
            pass

        # Check lookup table for other timezones.  This can also
        # be a translation layer.
        # E.g. "china = GMT+8"

        timezones = self.translate_namedvalues("timezone.value")
        for timezone in timezones:
            if locale.lower() == timezone.lower():
                # assumes translation is correct
                self.log.info('get_timezone: Final Timezone from Timezone Values: ' + timezones[timezone].strip())
                return (pytz.timezone(timezones[timezone].strip()), locale)

        # Match <Country> first using PyTZ and Geocode Country List by finding the Capital of the Country first
        try:
            capital = self.country_list[str(locale).lower()][1]
            place = capital + ' ' + locale
            result = self.get_city_data(capital, locale)
            if result:
                self.log.info('get_timezone: Final Timezone from Capital of the Country: ' + str(result[0]))
                return (pytz.timezone(result[0]), place)
        except:
            pass

        # Then match the <City> next
        result = self.get_city_data(locale)
        if result:
            for key in self.country_list.keys():
                if self.country_list[key][0] == result[1].decode('ascii'):
                    country = key
            place = locale + ' ' + country
            self.log.info('get_timezone: Final Timezone from Geocode: ' + str(result[0]))
            return (pytz.timezone(result[0]), place)

        # If not, match different combinations
        combinations = [
            ['city', 'country'],
            ['country', 'city'],
        ]
        locale_split = str(locale).lower().split(" ")
        results = []
        for i in range(0, len(locale_split)):
            for combination in combinations:
                locale_toparse = [" ".join(locale_split[:i]), " ".join(locale_split[i:])]
                city = ''
                country = ''

                for index in range(0, len(combination)):
                    if combination[index] == 'country':
                        country = locale_toparse[index]
                    elif combination[index] == 'city':
                        city = locale_toparse[index]

                try:
                    country_data = self.country_list[str(country).lower()]
                    city_data = self.get_city_data(str(city).lower(), country)
                    #self.log.info('get_timezone: Multi-locations: ' + str(country_data) + ' ' +str(city_data))
                    results.append([country, country_data, city, city_data])
                except:
                    pass
        
        if results:
            results = sorted(results, key = lambda a: a[3][2], reverse = True)
            place = str(results[0][2]) + ' ' + str(results[0][0])
            self.log.info('get_timezone: Multi-locations: ' + str(results[0][3][0]))
            return (pytz.timezone(results[0][3][0]), place)
            
        self.log.info('get_timezone: Final Timezone: None')
        return None

    def get_city_data(self, city, country=None):
        country_code = None

        if country:
            country_code = self.country_list[str(country).lower()][0]
        
        results =  sorted(self.city2tz_list[str(city).lower()], key = lambda a: a[2], reverse = True)

        if results:
            for result in results:
                if (country_code) and (result[1].decode('ascii') == country_code):
                    return result

            if (country_code) and (results[0].decode('ascii') != country_code):
                return None
            else:
                return results[0]

        return None

    def get_local_datetime(self, location, dtUTC=None):
        if not dtUTC:
            dtUTC = now_utc()
        if self.display_tz:
            # User requested times be shown in some timezone
            tz = self.display_tz
        else:
            tz = self.get_timezone(self.location_timezone)[0]

        if location:
            try:
                tz = self.get_timezone(location)[0]
            except:
                self.speak_dialog("time.tz.not.found", {"location": location})
                return None

        return dtUTC.astimezone(tz)

    def get_locationtz_list(self, basename, geonames_url):
        filename = basename + '.zip'
        if not os.path.exists(filename):
            self.log.info('Did it pass by here?')
            urlretrieve(urljoin(geonames_url, filename), filename)

        # parse it
        city2tz = defaultdict(set)
        ranking = (b'PPLQ', b'PPLH', b'PPLW', b'PPL', b'PPLX', b'PPLL', b'PPLS', b'STLMT', b'PPLF', b'PPLR', b'PPLA5', b'PPLA4', b'PPLA3', b'PPLA2', b'PPLA', b'PPLCH', b'PPLG', b'PPLC')

        with ZipFile(filename) as zf, zf.open(basename + '.txt') as file:
            for line in file:
                fields = line.split(b'\t')
                if fields:
                    name, asciiname, alternatenames = (fields[1:4])
                                
                    try:
                        featurecode = ranking.index(fields[7])
                    except:
                        featurecode = ranking.index(b'PPL')

                    countrycode = fields[8]
                    timezone = fields[-2].decode('utf-8').strip()

                    if timezone:

                        for city in [name, asciiname] + alternatenames.split(b','):
                            city = city.decode('utf-8').strip()

                            if city:
                                city2tz[city.lower()].add((timezone, countrycode, featurecode))

        zf.close()
        return city2tz

    def get_country_list(self, basename, geonames_url):
        filename = basename + '.txt'
        if not os.path.exists(filename):
            urlretrieve(urljoin(geonames_url, filename), filename)

        countries = {}
        with open(filename, 'r') as file:
            for line in file:
                if line[0] == '#':
                    continue

                fields = line.split('\t')

                if fields:
                    country_name = fields[4]
                    country_code = fields[0]
                    capital = fields[5]

                    if country_name:
                        countries[country_name.lower()] = (country_code, capital.lower())
        return countries

    def get_display_date(self, day=None, location=None):
        if not day:
            day = self.get_local_datetime(location)
        if self.config_core.get('date_format') == 'MDY':
            return day.strftime("%-m/%-d/%Y")
        else:
            return day.strftime("%Y/%-d/%-m")

    def get_display_current_time(self, location=None, dtUTC=None):
        # Get a formatted digital clock time based on the user preferences
        dt = self.get_local_datetime(location, dtUTC)
        if not dt:
            return None

        return nice_time(dt, self.lang, speech=False,
                         use_24hour=self.use_24hour)

    def get_spoken_current_time(self, location=None, dtUTC=None, force_ampm=False):
        # Get a formatted spoken time based on the user preferences
        dt = self.get_local_datetime(location, dtUTC)
        if not dt:
            return

        # speak AM/PM when talking about somewhere else
        say_am_pm = bool(location) or force_ampm

        s = nice_time(dt, self.lang, speech=True,
                      use_24hour=self.use_24hour, use_ampm=say_am_pm)
        # HACK: Mimic 2 has a bug with saying "AM".  Work around it for now.
        if say_am_pm:
            s = s.replace("AM", "A.M.")
        return s

    def display(self, display_time):
        if display_time:
            if self.platform == "mycroft_mark_1":
                self.display_mark1(display_time)
            self.display_gui(display_time)

    def display_mark1(self, display_time):
        # Map characters to the display encoding for a Mark 1
        # (4x8 except colon, which is 2x8)
        code_dict = {
            ':': 'CIICAA',
            '0': 'EIMHEEMHAA',
            '1': 'EIIEMHAEAA',
            '2': 'EIEHEFMFAA',
            '3': 'EIEFEFMHAA',
            '4': 'EIMBABMHAA',
            '5': 'EIMFEFEHAA',
            '6': 'EIMHEFEHAA',
            '7': 'EIEAEAMHAA',
            '8': 'EIMHEFMHAA',
            '9': 'EIMBEBMHAA',
        }

        # clear screen (draw two blank sections, numbers cover rest)
        if len(display_time) == 4:
            # for 4-character times, 9x8 blank
            self.enclosure.mouth_display(img_code="JIAAAAAAAAAAAAAAAAAA",
                                         refresh=False)
            self.enclosure.mouth_display(img_code="JIAAAAAAAAAAAAAAAAAA",
                                         x=22, refresh=False)
        else:
            # for 5-character times, 7x8 blank
            self.enclosure.mouth_display(img_code="HIAAAAAAAAAAAAAA",
                                         refresh=False)
            self.enclosure.mouth_display(img_code="HIAAAAAAAAAAAAAA",
                                         x=24, refresh=False)

        # draw the time, centered on display
        xoffset = (32 - (4*(len(display_time))-2)) / 2
        for c in display_time:
            if c in code_dict:
                self.enclosure.mouth_display(img_code=code_dict[c],
                                             x=xoffset, refresh=False)
                if c == ":":
                    xoffset += 2  # colon is 1 pixels + a space
                else:
                    xoffset += 4  # digits are 3 pixels + a space

        if self._is_alarm_set():
            # Show a dot in the upper-left
            self.enclosure.mouth_display(img_code="CIAACA", x=29, refresh=False)
        else:
            self.enclosure.mouth_display(img_code="CIAAAA", x=29, refresh=False)

    def _is_alarm_set(self):
        msg = self.bus.wait_for_response(Message("private.mycroftai.has_alarm"))
        return msg and msg.data.get("active_alarms", 0) > 0

    def display_gui(self, display_time):
        """ Display time on the Mycroft GUI. """
        self.gui.clear()
        self.gui['time_string'] = display_time
        self.gui['ampm_string'] = ''
        self.gui['date_string'] = self.get_display_date()
        self.gui.show_page('time.qml')

    def _is_display_idle(self):
        # check if the display is being used by another skill right now
        # or _get_active() == "TimeSkill"
        return self.enclosure.display_manager.get_active() == ''

    def update_display(self, force=False):
        # Don't show idle time when answering a query to prevent
        # overwriting the displayed value.
        if self.answering_query:
            return

        self.gui['time_string'] = self.get_display_current_time()
        self.gui['date_string'] = self.get_display_date()
        self.gui['ampm_string'] = '' # TODO

        if self.settings.get("show_time", False):
            # user requested display of time while idle
            if (force is True) or self._is_display_idle():
                current_time = self.get_display_current_time()
                if self.displayed_time != current_time:
                    self.displayed_time = current_time
                    self.display(current_time)
                    # return mouth to 'idle'
                    self.enclosure.display_manager.remove_active()
            else:
                self.displayed_time = None  # another skill is using display
        else:
            # time display is not wanted
            if self.displayed_time:
                if self._is_display_idle():
                    # erase the existing displayed time
                    self.enclosure.mouth_reset()
                    # return mouth to 'idle'
                    self.enclosure.display_manager.remove_active()
                self.displayed_time = None

    def _extract_location(self, utt):
        # if "Location" in message.data:
        #     return message.data["Location"]
        rx_file = self.find_resource('location.rx', 'regex')
        if rx_file:
            with open(rx_file) as f:
                for pat in f.read().splitlines():
                    pat = pat.strip()
                    if pat and pat[0] == "#":
                        continue
                    res = re.search(pat, utt)
                    if res:
                        try:
                            return res.group("Location")
                        except IndexError:
                            pass
        return None

    ######################################################################
    ## Time queries / display

    def handle_query_current_time(self, message):
        utt = message.data.get('utterance', "")
        #self.log.info(message.data.get('Location'))

        
        location = self._extract_location(utt)
        #location = message.data.get('Location')
        current_time = self.get_spoken_current_time(location)
        
        if not current_time:
            return

        # speak it
        if (location):
            try:
                timezone = self.get_timezone(location)[1]
                self.speak_dialog("time.current.with.timezone", {"time": current_time, "timezone": timezone})
            except:
                self.speak_dialog("time.tz.not.found", {"location": location})
        else:
            self.speak_dialog("time.current", {"time": current_time})

        # and briefly show the time
        self.answering_query = True
        self.enclosure.deactivate_mouth_events()
        self.display(self.get_display_current_time(location))
        time.sleep(5)
        mycroft.audio.wait_while_speaking()
        self.enclosure.mouth_reset()
        self.enclosure.activate_mouth_events()
        self.answering_query = False
        self.displayed_time = None

    @intent_handler(IntentBuilder("current_time_handler_simple").
                    require("Time").optionally("Location"))
    def handle_current_time_simple(self, message):
        self.log.info('Intent: Current Time, Parser: Adapt, Utterance: ' + message.data.get('utterance', "").lower())
        self.handle_query_current_time(message)

    @intent_file_handler("what.time.is.it.intent")
    def handle_query_current_time_padatious(self, message):
        self.log.info('Intent: Current Time, Parser: Padatious, Utterance: ' + message.data.get('utterance', "").lower())
        self.handle_query_current_time(message)

    def handle_query_future_time(self, message):
        utt = normalize(message.data.get('utterance', "").lower())
        #self.log.info(message.data.get('Location'))
        self.log.info(message.data.get('Offset'))
        extract = extract_datetime(utt)
        if extract:
            dt = extract[0]
            utt = extract[1]
        location = self._extract_location(utt)
        future_time = self.get_spoken_current_time(location, dt, True)
        if not future_time:
            return

        # speak it
        self.speak_dialog("time.future", {"time": future_time})

        # and briefly show the time
        self.answering_query = True
        self.enclosure.deactivate_mouth_events()
        self.display(self.get_display_current_time(location, dt))
        time.sleep(5)
        mycroft.audio.wait_while_speaking()
        self.enclosure.mouth_reset()
        self.enclosure.activate_mouth_events()
        self.answering_query = False
        self.displayed_time = None

    @intent_file_handler("what.time.will.it.be.intent")
    def handle_query_future_time_padatious(self, message):
        self.log.info('Intent: Future Time, Parser: Padatious, Utterance: ' + message.data.get('utterance', "").lower())
        self.handle_query_future_time(message)

    @intent_handler(IntentBuilder("").require("Display").require("Time").
                    optionally("Location"))
    def handle_show_time(self, message):
        self.display_tz = None
        utt = message.data.get('utterance', "")
        location = self._extract_location(utt)
        if location:
            tz = self.get_timezone(location)[0]
            if not tz:
                self.speak_dialog("time.tz.not.found", {"location": location})
                return
            else:
                self.display_tz = tz
        else:
            self.display_tz = None

        # show time immediately
        self.settings["show_time"] = True
        self.update_display(True)

    ######################################################################
    ## Date queries

    @intent_handler(IntentBuilder("").require("Query").require("Date").
                    optionally("Location"))
    def handle_query_date(self, message):
        utt = message.data.get('utterance', "").lower()
        extract = extract_datetime(utt)
        day = extract[0]

        # check if a Holiday was requested, e.g. "What day is Christmas?"
        year = extract_number(utt)
        if not year or year < 1500 or year > 3000:  # filter out non-years
            year = day.year
        all = {}
        # TODO: How to pick a location for holidays?
        for st in holidays.US.STATES:
            l = holidays.US(years=[year], state=st)
            for d, name in l.items():
                if not name in all:
                    all[name] = d
        for name in all:
            d = all[name]
            # Uncomment to display all holidays in the database
            # self.log.info("Day, name: " +str(d) + " " + str(name))
            if name.replace(" Day", "").lower() in utt:
                day = d
                break

        location = self._extract_location(utt)
        if location:
            # TODO: Timezone math!
            today = to_local(now_utc())
            if day.year == today.year and day.month == today.month and day.day == today.day:
                day = now_utc()  # for questions like "what is the day in sydney"
            day = self.get_local_datetime(location, dtUTC=day)
        if not day:
            return  # failed in timezone lookup

        speak = nice_date(day, lang=self.lang)
        # speak it
        self.speak_dialog("date", {"date": speak})

        # and briefly show the date
        self.answering_query = True
        self.show_date(location, day=day)
        time.sleep(10)
        mycroft.audio.wait_while_speaking()
        if self.platform == "mycroft_mark_1":
            self.enclosure.mouth_reset()
            self.enclosure.activate_mouth_events()
        self.answering_query = False
        self.displayed_time = None

    @intent_handler(IntentBuilder("").require("Query").require("Month"))
    def handle_day_for_date(self, message):
        self.handle_query_date(message)

    @intent_handler(IntentBuilder("").require("Query").require("RelativeDay"))
    def handle_query_relative_date(self, message):
        self.handle_query_date(message)

    @intent_handler(IntentBuilder("").require("RelativeDay").require("Date"))
    def handle_query_relative_date_alt(self, message):
        self.handle_query_date(message)

    @intent_file_handler("date.future.weekend.intent")
    def handle_date_future_weekend(self, message):
        # Strip year off nice_date as request is inherently close
        # Don't pass `now` to `nice_date` as a
        # request on Friday will return "tomorrow"
        saturday_date = ', '.join(nice_date(extract_datetime(
                        'this saturday')[0]).split(', ')[:2])
        sunday_date = ', '.join(nice_date(extract_datetime(
                      'this sunday')[0]).split(', ')[:2])
        self.speak_dialog('date.future.weekend', {
            'direction': 'next',
            'saturday_date': saturday_date,
            'sunday_date': sunday_date
        })

    @intent_file_handler("date.last.weekend.intent")
    def handle_date_last_weekend(self, message):
        # Strip year off nice_date as request is inherently close
        # Don't pass `now` to `nice_date` as a
        # request on Monday will return "yesterday"
        saturday_date = ', '.join(nice_date(extract_datetime(
                        'this saturday')[0]).split(', ')[:2])
        sunday_date = ', '.join(nice_date(extract_datetime(
                      'this sunday')[0]).split(', ')[:2])
        self.speak_dialog('date.last.weekend', {
            'direction': 'last',
            'saturday_date': saturday_date,
            'sunday_date': sunday_date
        })

    @intent_handler(IntentBuilder("").require("Query").require("LeapYear"))
    def handle_query_next_leap_year(self, message):
        now = datetime.datetime.now()
        leap_date = datetime.datetime(now.year, 2, 28)
        year = now.year if now <= leap_date else now.year + 1
        next_leap_year = self.get_next_leap_year(year)
        self.speak_dialog('next.leap.year', {'year': next_leap_year})

    def show_date(self, location, day=None):
        if self.platform == "mycroft_mark_1":
            self.show_date_mark1(location, day)
        self.show_date_gui(location, day)

    def show_date_mark1(self, location, day):
        show = self.get_display_date(day, location)
        self.enclosure.deactivate_mouth_events()
        self.enclosure.mouth_text(show)

    def get_weekday(self, day=None, location=None):
        if not day:
            day = self.get_local_datetime(location)
        return day.strftime("%A")

    def get_month_date(self, day=None, location=None):
        if not day:
            day = self.get_local_datetime(location)
        return day.strftime("%B %d")

    def get_year(self, day=None, location=None):
        if not day:
            day = self.get_local_datetime(location)
        return day.strftime("%Y")

    def get_next_leap_year(self, year):
        next_year = year + 1
        if self.is_leap_year(next_year):
            return next_year
        else:
            return self.get_next_leap_year(next_year)

    def is_leap_year(self, year):
        return (year % 400 == 0) or ((year % 4 == 0) and (year % 100 != 0))

    def show_date_gui(self, location, day):
        self.gui.clear()
        self.gui['date_string'] = self.get_display_date(day, location)
        self.gui['weekday_string'] = self.get_weekday(day, location)
        self.gui['month_string'] = self.get_month_date(day, location)
        self.gui['year_string'] = self.get_year(day, location)
        self.gui.show_page('date.qml')


def create_skill():
    return TimeSkill()
