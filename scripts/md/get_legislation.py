#!/usr/bin/env python
from lxml.html import fromstring
import datetime
import os
import re
import sys
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pyutils.legislation import (LegislationScraper, Bill, Vote, Legislator,
                                 NoDataForYear)

CHAMBERS = {
    'upper': ('SB','SJ'),
    'lower': ('HB','HJ'),
}
SESSIONS = {
    '2010': ('rs',),
    '2009': ('rs',),
    '2008': ('rs',),
    '2007': ('rs','s1'),
    '2006': ('rs','s1'),
    '2005': ('rs',),
    '2004': ('rs','s1'),
    '2003': ('rs',),
    '2002': ('rs',),
    '2001': ('rs',),
    '2000': ('rs',),
    '1999': ('rs',),
    '1998': ('rs',),
    '1997': ('rs',),
    '1996': ('rs',),
}

BASE_URL = "http://mlis.state.md.us"

# year, session, bill_type, number
BILL_URL = BASE_URL + "/%s%s/billfile/%s%04d.htm"

SEN_URL = "http://www.msa.md.gov/msa/mdmanual/05sen/html/senal.html"
DEL_URL = "http://www.msa.md.gov/msa/mdmanual/06hse/html/hseal.html"

class MDLegislationScraper(LegislationScraper):

    state = 'ex'

    metadata = {
        'state_name': 'Maryland',
        'legislature_name': 'Maryland General Assembly',
        'upper_chamber_name': 'Senate',
        'lower_chamber_name': 'House of Delegates',
        'upper_title': 'Senator',
        'lower_title': 'Delegate',
        'upper_term': 4,
        'lower_term': 4,
        'sessions': SESSIONS.keys(),
        'session_details': {
            '2007-2008': {'years': [2007, 2008], 'sub_sessions':
                              ['Sub Session 1', 'Sub Session 2']},
            '2009-2010': {'years': [2009, 2010], 'sub_sessions': []}}}
    
    def parse_bill_sponsors(self, doc, bill):
        sponsor_list = doc.cssselect('a[name=Sponlst]')
        if sponsor_list:
            # more than one bill sponsor exists
            elems = sponsor_list[0] \
                .getparent().getparent().getparent().cssselect('dd a')
            for elem in elems:
                bill.add_sponsor('cosponsor', elem.text.strip())
        else:
            # single bill sponsor
            sponsor = doc.cssselect('a[name=Sponsors]')[0] \
                .getparent().getparent().cssselect('dd a')[0].text.strip()
            bill.add_sponsor('primary', sponsor)
    
    def parse_bill_actions(self, doc, bill):
        for h5 in doc.cssselect('h5'):
            if h5.text in ('House Action', 'Senate Action'):
                chamber = 'upper' if h5.text == 'Senate Action' else 'lower'
                elems = h5.getnext().cssselect('dt')
                for elem in elems:
                    action_date = elem.text.strip()
                    if action_date != "No Action":
                        action_date = datetime.datetime.strptime(
                            "%s/%s" % (action_date, bill['session']), '%m/%d/%Y')
                        action_desc = ""
                        dd_elem = elem.getnext()
                        while dd_elem is not None and dd_elem.tag == 'dd':
                            if action_desc:
                                action_desc = "%s %s" % (action_desc, dd_elem.text.strip())
                            else:
                                action_desc = dd_elem.text.strip()
                            dd_elem = dd_elem.getnext()
                        bill.add_action(chamber, action_desc, action_date)
    
    def parse_bill_documents(self, doc, bill):
        elems = doc.cssselect('a[name=Exbill]')[0] \
            .getparent().getnext().getnext() \
            .cssselect('dt')[0].cssselect('b')
        for elem in elems:
            document_type = elem.text
            if document_type.startswith('Bill Text'):
                for sib in elem.itersiblings():
                    if sib.tag == 'a':
                        bill.add_version(sib.text.strip(','), BASE_URL + sib.get('href'))
            elif document_type.startswith('Fiscal and Policy Note'):
                for sib in elem.itersiblings():
                    if sib.tag == 'a' and sib.text == 'Available':
                        bill.add_document(document_type, BASE_URL + sib.get('href'))
            
    def scrape_bill(self, chamber, year, session, bill_type, number):
        """ Creates a bill object with the following attributes:
                * title
                * sponsors
        """

        url = BILL_URL % (year, session, bill_type, number)
        content = self.urlopen(url)
        doc = fromstring(content)

        # title
        # find <a name="Title">, get parent dt, get parent dl, then get dd within dl
        title = doc.cssselect('a[name=Title]')[0] \
            .getparent().getparent().cssselect('dd')[0].text.strip()
            
        # create the bill object now that we have the title
        bill = Bill(year, chamber, "%s %d" % (bill_type, number), title)
        bill.add_source(url)

        self.parse_bill_sponsors(doc, bill)     # bill sponsors
        self.parse_bill_actions(doc, bill)      # bill actions
        self.parse_bill_documents(doc, bill)    # bill documents and versions
        
        # add bill to collection
        self.add_bill(bill)
        
        print bill
        print "-" * 70
        
        time.sleep(5)
    
    def scrape_session(self, chamber, year, session):
        for bill_type in CHAMBERS[chamber]:
            for i in xrange(1, 2000):
                self.scrape_bill(chamber, year, session, bill_type, i)

    def scrape_bills(self, chamber, year):
        
        if year not in SESSIONS:
            raise NoDataForYear(year)
        
        for session in SESSIONS[year]:
            self.scrape_session(chamber, year, session)

        #         d1 = datetime.datetime.strptime('1/29/2010', '%m/%d/%Y')
        #         v1 = Vote('upper', d1, 'Final passage',
        #                   True, 2, 0, 0)
        #         v1.yes('Bob Smith')
        #         v1.yes('Sally Johnson')
        # 
        #         d2 = datetime.datetime.strptime('1/30/2010', '%m/%d/%Y')
        #         v2 = Vote('lower', d2, 'Final passage',
        #                   False, 0, 1, 1)
        #         v2.no('B. Smith')
        #         v2.other('Sally Johnson')
        # 
        #         b1.add_vote(v1)
        #         b1.add_vote(v2)
        
    def scrape_legislators(self, chamber, year):
        
        if year not in SESSIONS:
            raise NoDataForYear(year)
            
        # 
        #         l1 = Legislator('2009-2010', chamber, '1st',
        #                         'Bob Smith', 'Bob', 'Smith', '',
        #                         'Democrat')
        # 
        #         if chamber == 'upper':
        #             l1.add_role('President of the Senate', '2009-2010')
        #         else:
        #             l1.add_role('Speaker of the House', '2009-2010')
        # 
        #         l1.add_source('http://example.com/Bob_Smith.html')
        # 
        #         l2 = Legislator('2009-2010', chamber, '2nd',
        #                         'Sally Johnson', 'Sally', 'Johnson', '',
        #                         'Republican')
        #         l2.add_role('Minority Leader', '2009-2010')
        #         l2.add_source('http://example.com/Sally_Johnson.html')
        # 
        #         self.add_legislator(l1)
        #         self.add_legislator(l2)


if __name__ == '__main__':
    MDLegislationScraper.run()
