#!/usr/bin/env python
from lxml.html import fromstring, tostring
import datetime
import os
import re
import sys
import time
from urllib2 import HTTPError

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
BILL_URL = BASE_URL + "/%s%s/billfile/%s%04d.htm" # year, session, bill_type, number
SEN_URL = "http://www.msa.md.gov/msa/mdmanual/05sen/html/senal.html"
DEL_URL = "http://www.msa.md.gov/msa/mdmanual/06hse/html/hseal.html"

MOTION_RE = re.compile(r"(?P<motion>[\w\s]+) \((?P<yeas>\d{1,3})-(?P<nays>\d{1,3})\)")

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
                        try:
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
                        except ValueError:
                            pass # probably trying to parse a bad entry, not really an action
    
    def parse_bill_documents(self, doc, bill):
        for elem in doc.cssselect('b'):
            if elem.text:
                doc_type = elem.text.strip().strip(":")
                if doc_type.startswith('Bill Text'):
                    for sib in elem.itersiblings():
                        if sib.tag == 'a':
                            bill.add_version(sib.text.strip(','), BASE_URL + sib.get('href'))
                elif doc_type.startswith('Fiscal and Policy Note'):
                    for sib in elem.itersiblings():
                        if sib.tag == 'a' and sib.text == 'Available':
                            bill.add_document(doc_type, BASE_URL + sib.get('href'))
    
    def parse_bill_votes(self, doc, bill):
        """    def __init__(self, chamber, date, motion, passed,
                    yes_count, no_count, other_count, **kwargs):
        """
        params = {
            'chamber': bill['chamber'],
            'date': None,
            'motion': None,
            'passed': None,
            'yes_count': None,
            'no_count': None,
            'other_count': None,
        }
        elems = doc.cssselect('a')
        for elem in elems:
            href = elem.get('href')
            if href and "votes" in href:
                vote_url = BASE_URL + href
                vote_doc = fromstring(self.urlopen(vote_url))
                # motion
                for a in vote_doc.cssselect('a'):
                     if 'motions' in a.get('href'):
                        match = MOTION_RE.match(a.text)
                        if match:
                            motion = match.groupdict().get('motion', '').strip()
                            params['passed'] = 'Passed' in motion or 'Adopted' in motion
                            params['motion'] = motion
                            break
                # ugh
                bs = vote_doc.cssselect('b')[:5]
                yeas = int(bs[0].text.split()[0])
                nays = int(bs[1].text.split()[0])
                excused = int(bs[2].text.split()[0])
                not_voting = int(bs[3].text.split()[0])
                absent = int(bs[4].text.split()[0])
                params['yes_count'] = yeas
                params['no_count'] = nays
                params['other_count'] = excused + not_voting + absent
                
                # date
                # parse the following format: March 23, 2009 8:44 PM
                (date_elem, time_elem) = vote_doc.cssselect('table td font')[1:3]
                dt = "%s %s" % (date_elem.text.strip(), time_elem.text.strip())
                params['date'] = datetime.datetime.strptime(dt, '%B %d, %Y %I:%M %p')
                
                vote = Vote(**params)
                
                status = None
                for row in vote_doc.cssselect('table')[3].cssselect('tr'):
                    text = row.text_content()
                    if text.startswith('Voting Yea'):
                        status = 'yes'
                    elif text.startswith('Voting Nay'):
                        status = 'no'
                    elif text.startswith('Not Voting') or text.startswith('Excused'):
                        status = 'other'
                    else:
                        for cell in row.cssselect('a'):
                            getattr(vote, status)(cell.text.strip())
                
                bill.add_vote(vote)
                bill.add_source(vote_url)
                    
            
    def scrape_bill(self, chamber, year, session, bill_type, number):
        """ Creates a bill object
        """
        url = BILL_URL % (year, session, bill_type, number)
        content = self.urlopen(url)
        doc = fromstring(content)

        # title
        # find <a name="Title">, get parent dt, get parent dl, then get dd within dl
        title = doc.cssselect('a[name=Title]')[0] \
            .getparent().getparent().cssselect('dd')[0].text.strip()
            
        # create the bill object now that we have the title
        print "%s %d" % (bill_type, number)
        bill = Bill(year, chamber, "%s %d" % (bill_type, number), title)
        bill.add_source(url)

        self.parse_bill_sponsors(doc, bill)     # sponsors
        self.parse_bill_actions(doc, bill)      # actions
        self.parse_bill_documents(doc, bill)    # documents and versions
        self.parse_bill_votes(doc, bill)        # votes
        
        # add bill to collection
        self.add_bill(bill)
        
        #time.sleep(1)
    
    def scrape_session(self, chamber, year, session):
        for bill_type in CHAMBERS[chamber]:
            for i in xrange(1, 2000):
                try:
                    self.scrape_bill(chamber, year, session, bill_type, i)
                except HTTPError, he:
                    # hope this is because the page doesn't exist
                    # and not because something is broken
                    if he.code != 404:
                        raise he
                    break

    def scrape_bills(self, chamber, year):
        
        if year not in SESSIONS:
            raise NoDataForYear(year)
        
        for session in SESSIONS[year]:
            self.scrape_session(chamber, year, session)
        
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
        
        """
        ['__class__', '__contains__', '__copy__', '__deepcopy__', '__delattr__',
        '__delitem__', '__dict__', '__doc__', '__format__', '__getattribute__',
        '__getitem__', '__hash__', '__init__', '__iter__', '__len__', '__module__',
        '__new__', '__nonzero__', '__reduce__', '__reduce_ex__', '__repr__',
        '__reversed__', '__setattr__', '__setitem__', '__sizeof__', '__str__',
        '__subclasshook__', '__weakref__', '_init', '_label__del', '_label__get',
        '_label__set', 'addnext', 'addprevious', 'append', 'attrib', 'base',
        'base_url', 'body', 'clear', 'cssselect', 'drop_tag', 'drop_tree',
        'extend', 'find', 'find_class', 'find_rel_links', 'findall', 'findtext',
        'forms', 'get', 'get_element_by_id', 'getchildren', 'getiterator',
        'getnext', 'getparent', 'getprevious', 'getroottree', 'head', 'index',
        'insert', 'items', 'iter', 'iterancestors', 'iterchildren', 'iterdescendants',
        'iterfind', 'iterlinks', 'itersiblings', 'itertext', 'keys', 'label',
        'make_links_absolute', 'makeelement', 'nsmap', 'prefix', 'remove',
        'replace', 'resolve_base_href', 'rewrite_links', 'set', 'sourceline',
        'tag', 'tail', 'text', 'text_content', 'values', 'xpath']
        """


if __name__ == '__main__':
    MDLegislationScraper.run()
