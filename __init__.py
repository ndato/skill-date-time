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
from mycroft.api import Api

# For Location checking
import geocoder
from tzwhere import tzwhere
from countryinfo import CountryInfo

# For Holiday Checking
from .holidayapi import v1

class TimeSkill(MycroftSkill):

    def __init__(self):
        super(TimeSkill, self).__init__("TimeSkill")
        self.astral = Astral()
        self.displayed_time = None
        self.display_tz = None 
        self.answering_query = False

        self.holiday_cache = []
        self.holiday_cache_country_list = {}
        self.country_list = {}

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

        #Get all Country Names
        self.country_list = self.get_country_list()
        #self.log.info("initialize: Country List length: " + str(len(self.country_list)))

        #Temporary Implementation of Geonames API and TZWhere Library
        file = open(os.path.join(self.root_dir, 'geonames.key')) 
        self.username = file.read()
        file.close()
        self.tz = tzwhere.tzwhere()

        #Temporary Implementation of Holiday API
        file = open(os.path.join(self.root_dir, 'holidayapi.key'))
        self.hapi = v1(file.read())
        file.close()
        self.get_holiday_list(self.location['city']['state']['country']['code'], datetime.datetime.now().year)
        #self.log.info("initialize: Holiday List length: " + str(len(self.holiday_cache)))

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
        #self.log.info('get_timezone: Initial Locale: ' + str(locale))       
        try:
            # This handles codes like "America/Los_Angeles"
            #self.log.info('get_timezone: Final Timezone from PyTZ: ' + str(pytz.timezone(locale)))
            return (pytz.timezone(locale), locale)
        except:
            pass

        try:
            # This handles common city names, like "Dallas" or "Paris"
            #self.log.info('get_timezone: Final Timezone from Astral: ' + str(self.astral[locale].timezone))
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
                #self.log.info('get_timezone: Final Timezone from Timezone Values: ' + timezones[timezone].strip())
                return (pytz.timezone(timezones[timezone].strip()), locale)

        # Check if locale given is a country. tznames does not get the correct timezone because the bounding box from the Geonames API
        # gives the bounding box of the whole country. So we get the capital first, then get the timezone in the capital.
        try:
            country = CountryInfo(locale)
            locale = country.capital() + ' ' + locale
        except:
            pass
        
        # Use Geonames API as last resort
        timezone, place = self.get_timezone_geonames(locale)
        if (timezone) and (place):
            #self.log.info('get_timezone: Final Timezone from Geonames API: ' + timezone)
            return (pytz.timezone(timezone), place)
                
        #self.log.info('get_timezone: Final Timezone: None')
        return None

    # Temporary implementation. Should be in the GeonamesAPI class
    def get_location_data(self, search_string):
        #self.log.info('get_location_data: Got it from: ' + search_string)
        return geocoder.geonames(search_string, maxRows=1, key=self.username)

    # Temporary implementation. Should be in the GeonamesAPI class
    def get_timezone_geonames(self, search_string):
        location_data = self.get_location_data(search_string)

        if (location_data.address == location_data.country):
            place = location_data.country
        else:
            place = location_data.address + ' ' + location_data.country

        timezone = self.tz.tzNameAt(float(location_data.lat) ,float(location_data.lng))
        return (timezone, place)

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

    def get_country_list(self):
        country_list_temp = dict()

        for country in CountryInfo().all():
            #self.log.info(country)
            if str(country).lower().lstrip() != '':
                country_list_temp.update({str(country).lower().lstrip(): CountryInfo(country).iso(2)})
            
            try:
                for alt_spelling in CountryInfo(country).alt_spellings():
                    #self.log.info(alt_spelling)
                    if str(alt_spelling).lower().lstrip() != '':
                        country_list_temp.update({str(alt_spelling).lower().lstrip(): CountryInfo(country).iso(2)})
            except:
                #self.log.info("No Alternative Spelling for: " + str(country))
                pass

            try:
                #self.log.info(CountryInfo(country).native_name())
                if str(CountryInfo(country).native_name().lstrip()) != '':
                    country_list_temp.update({str(CountryInfo(country).native_name().lstrip()).lower(): CountryInfo(country).iso(2)})
            except:
                #self.log.info("No Native Name for: " + str(country))
                pass
            
        #self.log.info("get_country_list: Country List length: " + str(len(country_list_temp)))
        return country_list_temp

    def get_country_code(self, country_string): 
        while (len(self.country_list) == 0):
            time.sleep(0.25)

        #self.log.info('get_country_code: Getting String: ' + country_string)
        try:
            #self.log.info('get_country_code: Got String: ' + self.country_list[str(country_string).lower()])
            return self.country_list[str(country_string).lower()]
        except:
            #self.log.info('get_country_code: String Not Found: ' + str(country_string))
            return None

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
        #self.log.info('Intent: Current Time, Parser: Adapt, Utterance: ' + message.data.get('utterance', "").lower())
        self.handle_query_current_time(message)

    @intent_file_handler("what.time.is.it.intent")
    def handle_query_current_time_padatious(self, message):
        #self.log.info('Intent: Current Time, Parser: Padatious, Utterance: ' + message.data.get('utterance', "").lower())
        self.handle_query_current_time(message)

    def handle_query_future_time(self, message):
        utt = normalize(message.data.get('utterance', "").lower())
        #self.log.info(message.data.get('Location'))
        #self.log.info(message.data.get('Offset'))
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
        #self.log.info('Intent: Future Time, Parser: Padatious, Utterance: ' + message.data.get('utterance', "").lower())
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

    @intent_handler(IntentBuilder("").require("Query").require("Month").
                    optionally("Location"))
    def handle_day_for_date(self, message):
        self.handle_query_date(message)

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
    
    @intent_file_handler("when.is.holiday.intent")
    def handle_query_holiday_date(self, message):
        holiday = message.data.get('holiday')
        location = message.data.get('location')
        #self.log.info("handle_query_holiday_date: Query is Holiday. Holiday: " +  str(holiday) + " Location: " +  str(location))
        #self.log.info("handle_query_holiday_date: Default Location: " +  str(self.location['city']['state']['country']))

        country_code = None

        if location == None:
            #self.log.info("handle_query_holiday_date: No Location")
            country_code = self.location['city']['state']['country']['code']
        else:
            country_code = self.get_country_code(location)
            if country_code == None:
                #self.log.info("handle_query_holiday_date: Location not found: " + str(holiday) + ' ' + str(location))
                self.speak_dialog('holiday.with.location.not.found', {"holiday": str(holiday), "location": str(location)})
                return

        holiday_date = self.find_holiday_date(holiday.lower(), country_code)

        if holiday_date != None:
            #self.log.info("handle_query_holiday_date: Holiday found: " + str(holiday) + " Location: " + str(location) + " Date: " + str(holiday_date))
            if location == None:
                self.speak_dialog('holiday.date', {"holiday": str(holiday), "date": nice_date(datetime.datetime.strptime(holiday_date, '%Y-%m-%d'))})
            else:
                self.speak_dialog('holiday.date.with.location', {"holiday": str(holiday), "location": str(location), "date": nice_date(datetime.datetime.strptime(holiday_date, '%Y-%m-%d'))})
        else:
            #self.log.info("handle_query_holiday_date: Holiday not found: " + str(holiday) )
            if location == None:
                self.speak_dialog('holiday.not.found', {"holiday": str(holiday)})
            else:
                self.speak_dialog('holiday.with.location.not.found', {"holiday": str(holiday), "location": str(location)})


    def get_holiday_list(self, country_code, year):
        #self.log.info('get_holiday_list: Getting Holiday List: ' + location + ' ' + str(year))
        parameters = {
            'country':  country_code,
            'year':     year,
            'pretty':   True,
        }
        holiday_initial = []
        while (len(holiday_initial) == 0):
            try:
                holiday_initial = self.hapi.holidays(parameters)
            except:
                pass
            time.sleep(0.25)

        #for holiday in holiday_initial['holidays']:
        #    self.log.info(str(holiday))

        self.holiday_cache.extend(holiday_initial['holidays'])
        if (country_code in self.holiday_cache_country_list):
            if (year in self.holiday_cache_country_list[country_code]):
                self.holiday_cache_country_list[country_code].append(year)
        else:
            self.holiday_cache_country_list.update({country_code: [year]})

    def find_holiday_date(self, holiday_string, country_code):
        #self.log.info("find_holiday_date: Got the following: Holiday String: " + str(holiday_string) + " Country Code: " + str(country_code))
        #self.log.info("find_holiday_date: Holiday List length: " + str(len(self.holiday_cache)))
        year = datetime.datetime.now().year

        if (country_code not in self.holiday_cache_country_list) or (year not in self.holiday_cache_country_list[country_code]):
            #self.log.info("find_holiday_date: Had to retrieve : " + str(country_code))
            self.get_holiday_list(country_code, year)

        for holiday in self.holiday_cache:
            #self.log.info("find_holiday_date: Comparing with: " + str(holiday))
            if (holiday['name'].replace('\'', '').lower() == holiday_string.replace('\'', '').lower()) and (holiday['country'] == country_code):
                #self.log.info("find_holiday_date: Found: " + str(holiday['date']))
                return holiday['date']

        #self.log.info("find_holiday_date: Not Found")
        return None

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