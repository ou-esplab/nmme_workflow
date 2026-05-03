#!/usr/bin/env python
# coding: utf-8

from bs4 import BeautifulSoup
import codecs
import argparse
#from nmme_utils import getFcstDates
from datetime import datetime

# Eliminate Warnings
import warnings
warnings.filterwarnings("ignore")

# Parse commend line arguments
parser = argparse.ArgumentParser()
parser.add_argument("--date",nargs='?',default=None,help="make NMME forecasts based on this date")
args = parser.parse_args()

# Fcst Date Handling

if (args.date):
    fcstdate=args.date
else:
    sys.exit(print("Usage: Forecast Date of YYMM  Required"))

html_doc='forecasts.'+fcstdate+'.html'
f=codecs.open(html_doc,'r')
soup = BeautifulSoup(f, 'html.parser')

# Insert the new forecast date after Latest
tag=soup.option
new_option=soup.new_tag("option")
new_option.string=fcstdate #.strftime("%Y%m%d")
tag.insert_after(new_option)

# Write out the new file
ofname='output.'+new_option.string+'.html'
with open(ofname, "w") as file:
    print("Writing new forecasts.html file to",ofname)
    file.write(str(soup.prettify()))
