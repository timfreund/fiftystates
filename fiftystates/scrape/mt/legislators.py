import os
import csv

from fiftystates.scrape import NoDataForPeriod
from fiftystates.scrape.legislators import LegislatorScraper, Legislator
from fiftystates.scrape.mt import metadata

import html5lib
import lxml.html
from lxml.etree import ElementTree


class MTLegislatorScraper(LegislatorScraper):
    state = 'mt'

    def __init__(self, *args, **kwargs):
        super(MTLegislatorScraper, self).__init__(*args, **kwargs)
        self.parser = html5lib.HTMLParser(tree = html5lib.treebuilders.getTreeBuilder('lxml')).parse

        self.base_year = 1999
        self.base_term = 56

    def get_numeric_suffix(self, number):
        number = str(number)
        if str(number)[-2:] in ('11', '12', '13'):
            return 'th'
        last_digit = str(number)[-1:]
        if last_digit in ('0', '4', '5', '6', '7', '8', '9'):
            return 'th'
        elif last_digit in ('1'):
            return 'st'
        elif last_digit in ('2'):
            return 'nd'
        elif last_digit in ('3'):
            return 'rd'

    def scrape(self, chamber, term):
        if term < self.base_term:
            raise NoDataForPeriod(term)

        suffix = self.get_numeric_suffix(term)
        if term < 58:
            self.scrape_pre_58_legislators(chamber, term, suffix)
        else:
            self.scrape_legislators(chamber, term, suffix)

    def scrape_pre_58_legislators(self, chamber, term, suffix):
        url = 'http://leg.mt.gov/css/Terms/%d%s/legname.asp' % (term, suffix)
        legislator_page = ElementTree(lxml.html.fromstring(self.urlopen(url)))

        if term == 57:
            if chamber == 'upper':
                tableName = '57th Legislatore Roster Senate (2001-2002)'
                startRow = 3
            else:
                tableName = '57th Legislator Roster (House)(2001-2002)'
                startRow = 5
        elif term == 56:
            if chamber == 'upper':
                tableName = 'Members of the Senate'
                startRow = 3
            else:
                tableName = 'Members of the House'
                startRow = 5

        for table in legislator_page.xpath("//table"):
            if table.attrib.has_key('name') and table.attrib['name'] == tableName:
                parse_names = False
                for row in table.getchildren():
                    if row.tag != 'tr':
                        continue
                    celldata = row.getchildren()[0].text_content().strip()
                    if parse_names and len(celldata) != 0:
                        name, party_letter = celldata.rsplit(' (', 1)
                        party_letter = party_letter[0]

                        nameParts = [namePart.strip() for namePart in name.split(',')]
                        assert len(nameParts) < 4
                        if len(nameParts) == 2:
                            last_name, first_name = nameParts
                        elif len(nameParts) == 3:
                            last_name = ' '.join(nameParts[0:2])
                            first_name = nameParts[2]
                        else:
                            name, party_letter = celldata.rsplit(' (', 1)

                        district = row.getchildren()[2].text_content().strip()

                        if party_letter == 'R':
                            party = 'Republican'
                        elif party_letter == 'D':
                            party = 'Democrat'
                        else:
                            party = party_letter

                        legislator = Legislator(term, chamber, district, '%s %s' % (first_name, last_name), \
                                                first_name, last_name, '', party)
                        legislator.add_source(url)
                        self.save_legislator(legislator)

                    if celldata == "Name (Party)":
                        # The table headers seem to vary in size, but the last row
                        # always seems to start with 'Name (Party)' -- once we find
                        # that, start parsing legislator names
                        parse_names = True

    def scrape_legislators(self, chamber, term, suffix):
        year = 0
        for term_data in metadata['terms']:
            if term_data['name'] == term:
                year = term_data['start_year']

        url = 'http://leg.mt.gov/content/sessions/%s%s/%d%sMembers.txt' % \
            (term, suffix, year, chamber == 'upper' and 'Senate' or 'House')

        # 2009 Senate is different than
        if year > 2008 and chamber == 'upper':
            csv_parser = csv.reader(self.urlopen(url).split(os.linesep), delimiter = '\t')
            #Discard title row
            csv_parser.next()
        else:
            csv_parser = csv.reader(self.urlopen(url).split(os.linesep))

        for entry in csv_parser:
            if not entry:
                continue
            if year == 2003:
                first_name, last_name = entry[0].split(' ', 2)[1:3]
                party_letter = entry[1]
                district = entry[2]
            else:
                last_name = entry[0]
                first_name = entry[1]
                party_letter = entry[2]
                district = entry[3]#.split('D ')[1]
            if party_letter == '(R)':
                party = 'Republican'
            elif party_letter == '(D)':
                party = 'Democrat'
            else:
                party = party_letter
            first_name = first_name.capitalize()
            last_name = last_name.capitalize()
            #All we care about is the number
            district = district.split(' ')[1]

            legislator = Legislator(term, chamber, district, '%s %s' % (first_name, last_name), \
                                    first_name, last_name, '', party)
            legislator.add_source(url)
            self.save_legislator(legislator)
