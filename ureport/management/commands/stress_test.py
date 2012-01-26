from django.core.management.base import BaseCommand
from rapidsms.models import Contact
from rapidsms.models import Connection,Backend
from optparse import OptionParser, make_option
import random
from django.test.client import Client
from django.db import transaction
import subprocess

from uganda_common.utils import ExcelResponse
class Command(BaseCommand):
    option_list = BaseCommand.option_list + (
        make_option("-n", "--n", dest="n"),
        make_option("-f", "--f", dest="f"),

    )

    def handle(self, **options):


       for i in range(0,int(options['n'])):

           backend=Backend.objects.get(name="dmark")
           identity="259772%s"%str(random.randint(0000,1000))
           print identity
           try:
               conn,created=Connection.objects.get_or_create(backend=backend,identity=identity)
               if created:
                   contact=Contact.objects.create(name="Anonymous User")
                   conn.contact=contact
                   conn.save()
           except:
               #transaction.rollback()
               continue
               
       #create poll
       client = Client(enforce_csrf_checks=True)
       log_response=client.login(username='test1', password='testpass')
       print log_response
       import datetime


       #simulate 1000 requests and create poll
       print "participants in poll" + Contact.objects.count()
       poll_dict = {
           'groups': [1, 2, 5, 7, 54, 55, 97, 98, 99, 100, 104, 116, 121, 122, 125, 126, 127, 128, 129, 130, 131, 132,
                      133, 134, 135, 136, 137, 138, 139, 140, 141, 142, 143], 'default_response': 'poll test response',
           'name': 'poll test', 'start_immediately': 'on', 'question': 'poll test qn', 'response_type': 'a', 'type': 'yn',
           'question_luo': 'poll test qn luo', 'default_response_luo': ''}
       now = datetime.datetime.now()
       print now
       args='["nohup","sh","%s","&"]'%options['f']
       subprocess.Popen(eval(args))
       response = client.post('http://test.ureport.unicefuganda.org/createpoll/', poll_dict)
       print datetime.datetime.now()-now


