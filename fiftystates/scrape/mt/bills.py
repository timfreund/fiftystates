import logging
import os
import re
import sys
from datetime import datetime
from optparse import make_option, OptionParser

from fiftystates.scrape import NoDataForPeriod
from fiftystates.scrape.bills import BillScraper, Bill
from fiftystates.scrape.votes import Vote
from fiftystates.scrape.mt import metadata

import html5lib
import lxml.html
from lxml.etree import ElementTree
from scrapelib import HTTPError

action_map = {
    "Returned with Governor's Line-item Veto": 'governor:vetoed:line-item',
    'Introduced': 'bill:introduced',
    'Referred to Committee': 'committee:referred',
    'Rereferred to Committee': 'committee:referred',
    'Signed by Governor': 'governor:signed',
    'Taken from 2nd Reading; Rereferred to Committee': 'committee:referred',
    'Vetoed by Governor': 'governor:vetoed',
    }

actor_map = {
    '(S)': 'upper',
    '(H)': 'lower',
    '(C)': 'clerk',
    }

sponsor_map = {
    'Primary Sponsor': 'primary'
    }

vote_passage_indicators = ['Adopted',
                           'Appointed',
                           'Carried',
                           'Concurred',
                           'Dissolved',
                           'Passed',
                           'Rereferred to Committee',
                           'Transmitted to',
                           'Veto Overidden',
                           'Veto Overridden']
vote_failure_indicators = ['Failed',
                           'Rejected',
                           ]
vote_ambiguous_indicators = [
    'Indefinitely Postponed',
    'On Motion Rules Suspended',
    'Pass Consideration',
    'Reconsidered Previous',
    'Rules Suspended',
    'Segregated from Committee',
    'Special Action',
    'Sponsor List Modified',
    'Tabled',
    'Taken from']

class NoVoteDataException(Exception):
    pass

class MTBillScraper(BillScraper):
    #must set state attribute as the state's abbreviated name
    state = 'mt'

    def __init__(self, *args, **kwargs):
        super(MTBillScraper, self).__init__(*args, **kwargs)
        self.parser = html5lib.HTMLParser(tree = html5lib.treebuilders.getTreeBuilder('lxml')).parse

        self.search_url_template = "http://laws.leg.mt.gov/laws%s/LAW0203W$BSRV.ActionQuery?P_BLTP_BILL_TYP_CD=%s&P_BILL_NO=%s&P_BILL_DFT_NO=&Z_ACTION=Find&P_SBJ_DESCR=&P_SBJT_SBJ_CD=&P_LST_NM1=&P_ENTY_ID_SEQ="

    def getTerm(self, year):
        for term in metadata['terms']:
            if term['start_year'] == year:
                return term

    def scrape(self, chamber, year):
        year = int(year)
        #2 year terms starting on odd year, so if even number, use the previous odd year
        if year < 1999:
            raise NoDataForPeriod(year)
        if year % 2 == 0:
            year -= 1

        term = self.getTerm(year)

        for session in term['sessions']:
            try:
                session = int(session)
                if session == 1999:
                    base_bill_url = 'http://data.opi.mt.gov/bills/BillHtml/'
                else:
                    base_bill_url = 'http://data.opi.mt.gov/bills/%d/BillHtml/' % session
            except ValueError:
                special_session_urls = {
                    '1999 Special Session': 'http://data.opi.mt.gov/bills/Specsess/0699/BillHtml/',
                    '2000 Special Session': 'http://data.opi.mt.gov/bills/Specsess/0500/BillHtml/',
                    '2002 August Special Session': 'http://data.opi.mt.gov/bills/Specsess/0802/BillHtml/',
                    # http://leg.mt.gov/css/Sessions/Special%20Session/sept_2002/default.asp
                    # There was an immediate effective date inserted into legislation created in the
                    # August 2002 session.  The data is in a format that we can't parse at this time.
                    '2002 September Special Session': None,
                    '2005 December Special Session': 'http://data.opi.mt.gov/bills/Specsess/1205/BillHtml/',
                    '2007 May Special Session': 'http://data.opi.mt.gov/bills/Specsess/0507/BillHtml/',
                    '2007 September Special Session': 'http://data.opi.mt.gov/bills/Specsess/0907/BillHtml/',
                    }
                if special_session_urls.has_key(session):
                    base_bill_url = special_session_urls[session]
                else:
                    self.logger.error("No bill URL available for session %s" % session)

            for bill_url in self.get_bill_urls(base_bill_url, chamber):
                bill = self.parse_bill(bill_url, term, str(session), chamber)
                self.save_bill(bill)

    def get_bill_urls(self, base_bill_url, chamber):
        bill_urls = []

        if base_bill_url is None:
            return bill_urls
        
        index_page = ElementTree(lxml.html.fromstring(self.urlopen(base_bill_url)))
        for bill_anchor in index_page.findall('//a'):
            # See 2009 HB 645
            if bill_anchor.text.find("govlineveto") == -1:
                # House bills start with H, Senate bills start with S
                if chamber == 'lower' and bill_anchor.text.startswith('H'):
                    bill_urls.append("%s%s" % (base_bill_url, bill_anchor.text))
                elif chamber == 'upper' and bill_anchor.text.startswith('S'):
                    bill_urls.append("%s%s" % (base_bill_url, bill_anchor.text))
        return bill_urls

    def parse_bill(self, bill_url, term, session, chamber):
        bill = None
        bill_page = ElementTree(lxml.html.fromstring(self.urlopen(bill_url)))
        for anchor in bill_page.findall('//a'):
            if (anchor.text_content().startswith('status of') or
                anchor.text_content().startswith('Detailed Information (status)')):
                status_url = anchor.attrib['href'].replace("\r", "").replace("\n", "")
                bill = self.parse_bill_status_page(status_url, bill_url, term, session, chamber)
            elif anchor.text_content().startswith('This bill in WP'):
                index_url = anchor.attrib['href']
                index_url = index_url[0:index_url.rindex('/')]
                # this looks weird.  See http://data.opi.mt.gov/bills/BillHtml/SB0002.htm for why
                index_url = index_url[index_url.rindex("http://"):]
                self.add_bill_versions(bill, index_url)

        if bill is None:
            # No bill was found.  Maybe something like HB0790 in the 2005 term?
            # We can search for the bill metadata.
            page_name = bill_url.split("/")[-1].split(".")[0]
            bill_type = page_name[0:2]
            bill_number = page_name[2:]
            laws_year = str(term['start_year'])[2:]

            status_url = self.search_url_template % (laws_year, bill_type, bill_number)
            bill = self.parse_bill_status_page(status_url, bill_url, term, session, chamber)
        return bill

    def parse_bill_status_page(self, status_url, bill_url, term, session, chamber):
        bill = None
        bill_id = None
        sources = [bill_url, status_url]
        status_page = ElementTree(lxml.html.fromstring(self.urlopen(status_url)))

        if status_url == 'http://leg.mt.gov/css/sessions/special%20session/august_2002/bills/sb0001.asp':
            import pdb; pdb.set_trace()
        
        # see 2007 HB 2... weird.
        try:
            bill_id = status_page.xpath("/div/form[1]/table[2]/tr[2]/td[2]")[0].text_content()
        except IndexError:
            try:
                bill_id = status_page.xpath('/html/html[2]/tr[1]/td[2]')[0].text_content()
            except IndexError:
                pass
        if bill_id is None:
            try:
                bill_table = self.get_bill_table(status_page)
                bill_id = bill_table.xpath('//tr[2]/td[2]')[0].text_content()
                # bill_id = status_page.xpath('/html/body/table[4]/tr/td[2]/table/tr/td[1]/table[1]/tbody/tr[2]/td[2]')[0].text_content()
                # bill_id = status_page.xpath('/html/body/table[4]/tr/td[2]/table/tr[1]/td[1]/table/tr[2]/td[2]')[0].text_content()
                bill = self.parse_special_session_bill_status_page(bill_id,
                                                                   status_page,
                                                                   bill_table,
                                                                   session,
                                                                   chamber,
                                                                   sources)
            except IndexError:
                pass
        else:
            bill = self.parse_standard_bill_status_page(bill_id,
                                                        status_page,
                                                        session,
                                                        chamber,
                                                        sources)

        if bill is None:
            self.logger.error("No bill parsed for %s" % bill_url)
        return bill

    def parse_special_session_bill_status_page(self, bill_id, status_page, bill_table, session, chamber, sources):
        title = bill_table.xpath('//tr[3]/td[2]')[0].text_content()
        bill = Bill(session, chamber, bill_id, title)
        for source in sources:
            bill.add_source(source)
        self.add_sponsors(bill, self.get_sponsor_table(status_page))
        self.add_actions(bill, self.get_action_table(status_page))
        return bill

    def parse_standard_bill_status_page(self, bill_id, status_page, session, chamber, sources):
        try:
            title = status_page.xpath("/div/form[1]/table[2]/tr[3]/td[2]")[0].text_content()
        except IndexError:
            if len(status_page.xpath("/html/html")) == 2:
                title = status_page.xpath('/html/html[2]/tr[1]/td[2]')[0].text_content()
            else:
                title = status_page.xpath('/html/html[3]/tr[1]/td[2]')[0].text_content()

        bill = Bill(session, chamber, bill_id, title)
        for source in sources:
            bill.add_source(source)
        self.add_sponsors(bill, self.get_sponsor_table(status_page))
        self.add_actions(bill, self.get_action_table(status_page))

        return bill

    def get_bill_table(self, status_page):
        for table in status_page.xpath('//table'):
            table = ElementTree(table)
            if ((len(table.xpath('//tr')) == 4) and
                (table.xpath('//tr[1]/td[1]')[0].text_content().strip().startswith('Bill Draft Number:'))):
                return table

    def get_action_table(self, status_page):
        for table in status_page.xpath('//table'):
            table = ElementTree(table)
            if ((len(table.xpath('//th')) == 5) and
                (table.xpath('//th')[0].text_content().startswith('Action'))):
                return table

    def get_sponsor_table(self, status_page):
        for table in status_page.xpath('//table'):
            table = ElementTree(table)
            if ((len(table.xpath('//th')) == 4) and
            (table.xpath('//th')[0].text_content().startswith('Sponsor,'))):
                return table

    def add_actions(self, bill, action_table):
        for action in action_table.xpath('//tr')[1:]:
            try:
                # We previously split the action on spaces and took the first token
                # to find the actor, but the actor and action run together due to a
                # typo in http://leg.mt.gov/css/sessions/special%20session/may_2000/bills/hb0001.asp
                # (search for "(S)Hearing to find the offending line)
                actor = actor_map[action.xpath("td[1]")[0].text_content().strip()[0:3]]
                action_name = action.xpath("td[1]")[0].text_content().replace(actor, "")[4:].strip()
            except KeyError:
                actor = ''
                action_name = action.xpath("td[1]")[0].text_content().strip()
                if action_name == "Chapter Number Assigned":
                    actor = "clerk"

            action_date = datetime.strptime(action.xpath("td[2]")[0].text, '%m/%d/%Y')
            vote_url = None
            if len(action.xpath("td[3]/a")) == 1:
                vote_url = action.xpath("td[3]/a")[0].attrib['href']
            action_votes_yes = action.xpath("td[3]")[0].text_content().replace("&nbsp", "")
            action_votes_no = action.xpath("td[4]")[0].text_content().replace("&nbsp", "")
            action_committee = action.xpath("td[5]")[0].text
            if action_committee is None:
                # http://leg.mt.gov/css/sessions/special%20session/may_2000/bills/sb0001.asp
                action_committee = ''
            else:
                action_committee.replace("&nbsp", "")

            action_type = "other"
            if action_map.has_key(action_name):
                action_type = action_map[action_name]
            bill.add_action(actor, action_name, action_date, type=action_type)

            vote = None
            if vote_url:
                try:
                    vote = self.get_vote_results(bill, action_date, action_name, vote_url)
                except NoVoteDataException:
                    self.logger.warning("NoVoteDataException for %s %s" % (bill['session'], bill['bill_id']))

            if vote is None or vote['passed'] is None:
                vote = self.guess_vote_results(bill,
                                               action_votes_yes,
                                               action_votes_no,
                                               action_name,
                                               action_date,
                                               vote)

            if vote is not None:
                bill.add_vote(vote)

    def get_vote_results(self, bill, vote_date, motion_name, vote_url):
        """
        Vote URLs come in two forms: relative and absolute.
        Vote results come in two forms: HTML and TXT.
        This method ensures that we have an absolute URL, downloads the
        vote data, and does a bit of inspection to determine if we should
        parse the results as HTML or TXT.
        """

        if not vote_url.count("http"):
            for source in bill['sources']:
                if source['url'].count("laws.leg.mt.gov"):
                    vote_url = "%s/%s" % (source['url'][0:source['url'].rfind('/')],
                                          vote_url)

        vote_data = None
        try:
            vote_data = self.urlopen(vote_url)
        except HTTPError, he:
            if he.response.code == 404:
                raise NoVoteDataException(he)
            else:
                raise he
        
        if vote_data[0:6].upper() == "<HTML>":
            vote = self.get_html_vote_results(bill, motion_name, vote_data)
        elif vote_data[0:21].upper() == "UNOFFICIAL VOTE TALLY":
            vote = self.get_text_vote_results(bill, vote_date, motion_name, vote_data)
        else:
            self.logger.error("unknown vote format")

        if vote is not None:
            vote.add_source(vote_url)
        return vote

    def get_html_vote_results(self, bill, motion_name, vote_data):
        vote = Vote(bill['chamber'], None, motion_name, False, 0, 0, 0)

        if vote_data.count("No Vote Records Found for this Action.") > 0:
            raise NoVoteDataException()

        passage_indicators = ['Do Pass', 'Do Concur']
        for line in vote_data.splitlines():
            if line in passage_indicators:
                vote['passed'] = True
        
        vote_data = ElementTree(lxml.html.fromstring(vote_data))
        for table in vote_data.findall("//table"):
            left_header = table.findall("tr")[0].findall("th")[0].text.strip()
            if 'YEAS' == left_header:
                count_row = table.findall("tr")[-1]
                vote['yes_count'] = int(count_row.findall("td")[0].text)
                vote['no_count'] = int(count_row.findall("td")[1].text)
                other_count = int(count_row.findall("td")[2].text)
                vote['other_count'] = int(count_row.findall("td")[3].text) + other_count
            elif (('' == left_header) and (4 == len(table.findall("tr")[0].findall("th")))):
                for data in ElementTree(table).findall("//td"):
                    vote_value, name = data.text.replace(u"\xa0", " ").split(" ", 1)
                    vote_value = vote_value.strip()
                    name = name.strip()

                    if name != "":
                        if vote_value == 'Y':
                            vote.yes(name)
                        elif vote_value == 'N':
                            vote.no(name)
                        else:
                            vote.other(name)
            elif (('' == left_header) and (0 == table.findall("tr")[1].findall("td")[0].text.find("DATE:"))):
                date = table.findall("tr")[1].findall("td")[0].text
                date = datetime.strptime(date.replace("DATE:", "").strip(), "%B %d, %Y")
                vote['date'] = date
        return vote

    def get_text_vote_results(self, bill, vote_date, motion_name, vote_data):
        vote = Vote(bill['chamber'], vote_date, motion_name, None, 0, 0, 0)
        counting_yeas = False
        counting_nays = False

        for line in vote_data.splitlines():
            if line.find("Motion:") == 0:
                line = line.strip().upper()
                for x in ['DO CONCUR', 'DO PASS', 'DO ADOPT', ]:
                    if line.find(x) >= 0:
                        vote['passed'] = True
            elif ((line.find("Yeas:") == 0) or (line.find("Ayes:") == 0)):
                counting_yeas = True
                counting_nays = False
            elif ((line.find("Nays:") == 0) or (line.find("Noes") == 0)):
                counting_yeas = False
                counting_nays = True
            elif line.find("Total ") == 0:
                if not (counting_yeas or counting_nays):
                    vote['other_count'] += int(line.split()[1].strip())
            elif line == '':
                counting_yeas = False
                counting_nays = False

            if counting_yeas:
                if line.find("Total ") == 0:
                    vote['yes_count'] = int(line.split()[1].strip())
                    line = ""
                if line.find(":") != -1:
                    line = line[line.find(":")+1:]
                for name in line.split(","):
                    name = name.strip()
                    if name != '':
                        if name[-1] == '.':
                            name = name[0:-1]
                        vote.yes(name)

            if counting_nays:
                if line.find("Total ") == 0:
                    vote['no_count'] = int(line.split()[1].strip())
                    line = ""
                if line.find(":") != -1:
                    line = line[line.find(":")+1:]
                for name in line.split(","):
                    name = name.strip()
                    if name != '':
                        if name[-1] == '.':
                            name = name[0:-1]
                        vote.no(name)

        return vote

    def guess_vote_results(self, bill, votes_yes, votes_no, action_name, action_date, vote=None):
        """
        The fact that this method even exists makes a clear case for the Open State Project.
        At this point we've tried to parse detailed vote results if they exist, and we will now
        look at varied lists of passage and failure indicators to see if this vote matches any
        of the known indicators.
        """
        
        self.logger.debug("Educated passage guess for %s (%s)" % (bill['sources'][0]['url'], action_name))
        passed = None
        try:
            votes_yes = int(votes_yes)
            votes_no = int(votes_no)
        except ValueError, ve:
            return None

        if vote is None:
            vote = Vote(bill['chamber'],
                        action_date,
                        action_name,
                        passed,
                        votes_yes,
                        votes_no,
                        0)
            
        # some actions take a super majority, so we aren't just comparing the yeas and nays here.
        for i in vote_passage_indicators:
            if action_name.count(i):
                passed = True
        for i in vote_failure_indicators:
            if action_name.count(i) and passed == True:
                # a quick explanation:  originally an exception was
                # thrown if both passage and failure indicators were
                # present because I thought that would be a bug in my
                # lists.  Then I found 2007 HB 160.
                # Now passed = False if the nays outnumber the yays..
                # I won't automatically mark it as passed if the yays
                # ounumber the nays because I don't know what requires
                # a supermajority in MT.
                if votes_no >= votes_yes:
                    passed = False
                else:
                    raise Exception ("passage and failure indicator both present: %s" % action_name)
            if action_name.count(i) and passed == None:
                passed = False
        for i in vote_ambiguous_indicators:
            if action_name.count(i):
                passed = votes_yes > votes_no
        if passed is None:
            raise Exception("Unknown passage: %s" % action_name)
        else:
            vote['passed'] = passed
            
        return vote

    def add_sponsors(self, bill, sponsor_table):
        for sponsor_row in sponsor_table.xpath('//tr')[1:]:
            sponsor_type = sponsor_row.xpath("td[1]")[0].text
            sponsor_last_name = sponsor_row.xpath("td[2]")[0].text
            sponsor_first_name = sponsor_row.xpath("td[3]")[0].text
            sponsor_middle_initial = sponsor_row.xpath("td[4]")[0].text
            if sponsor_middle_initial is None:
                # http://leg.mt.gov/css/sessions/special%20session/may_2000/bills/sb0001.asp
                sponsor_middle_initial = ''
            sponsor_middle_initial = sponsor_middle_initial.replace("&nbsp", "")
            sponsor_full_name = "%s, %s %s" % (sponsor_last_name,  sponsor_first_name, sponsor_middle_initial)
            sponsor_full_name = sponsor_full_name.strip()

            if sponsor_map.has_key(sponsor_type):
                sponsor_type = sponsor_map[sponsor_type]
            bill.add_sponsor(sponsor_type, sponsor_full_name)

    def add_bill_versions(self, bill, index_url):
        # This method won't pick up bill versions where the bill is published
        # exclusively in PDF.  See 2009 HB 645 for a sample
        index_page = ElementTree(lxml.html.fromstring(self.urlopen(index_url)))
        tokens = bill['bill_id'].split(" ")
        bill_regex = re.compile("%s0*%s\_" % (tokens[0], tokens[1]))
        for anchor in index_page.findall('//a'):
            if bill_regex.match(anchor.text_content()) is not None:
                file_name = anchor.text_content()
                version = file_name[file_name.find('_')+1:file_name.find('.')]
                version_title = 'Final Version'
                if version != 'x':
                    version_title = 'Version %s' % version

                version_url = index_url[0:index_url.find('bills')-1] + anchor.attrib['href']
                bill.add_version(version_title, version_url)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s " + 'mt' +
                               " %(message)s",
                        datefmt="%H:%M:%S",
                       )


    option_list = (
        make_option('-c', '--chamber', action='store', dest='chamber',
                    help='chamber to scrape [upper|lower]'),
        make_option('-b', '--billurl', action='append', dest='billurls',
                    help='session(s) to scrape'),
        make_option('-t', '--term', action='store', dest='term',
                    help='term to scrape'),
        make_option('-s', '--session', action='store', dest='session',
                    help='session to scrape'),
        )
    parser = OptionParser(option_list=option_list)
    options, spares = parser.parse_args()

    options_validated = True
    for name in ['term', 'session', 'chamber', 'billurls']:
        if getattr(options, name) is None:
            print "No %s specified" % name
            options_validated = False

    term = None
    for t in metadata['terms']:
        if t['name'] == options.term:
            term = t

    if term is None:
        print "Invalid term"
        options_validated = False
        
    if not options_validated:
        sys.exit(-1)

    scraper = MTBillScraper(metadata, output_dir="./output/")
    for url in options.billurls:
        print term
        scraper.save_bill(scraper.parse_bill(url, term, options.session, options.chamber))

