#!/usr/bin/python
# -*- coding: utf-8 -*-

from django.shortcuts import render_to_response, get_object_or_404, redirect
from django.template import RequestContext
from django.utils import simplejson
from django.utils.safestring import mark_safe
from django.http import HttpResponse, HttpResponseRedirect
from ureport.settings import drop_words, tag_cloud_size
from ureport.models import *
from poll.models import *
from script.models import ScriptStep
from contact.models import MessageFlag
from utils import get_flagged_messages
from uganda_common.utils import ExcelResponse

from rapidsms_httprouter.views import receive
from rapidsms_httprouter.models import Message


from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.paginator import Paginator
from django.utils.datastructures import SortedDict
from django.template.loader import render_to_string

from generic.views import generic, generic_dashboard

from contact.models import  Flag
from utils import retrieve_poll
from ureport.forms import *
from generic.forms import StaticModuleForm
from generic.models import Dashboard
from django.core.files import File
from xlrd import open_workbook
from uganda_common.utils import assign_backend
from script.utils.handling import find_closest_match
from django.views.decorators.cache import cache_control
from models import Ureporter
from django.core.urlresolvers import reverse
from django.contrib.auth.decorators import login_required,\
    permission_required

from contact.forms import FlaggedMessageForm
from.utils import create_poll
from rapidsms_xforms.models import  XFormField
from ussd.models import Menu,  Field, Question,StubScreen
from django.template import Context, Template
from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.http import Http404, HttpResponse
from django.db.models import get_model
from .forms import GroupRules

import re
import bisect
import textwrap
import random
import datetime
import types
from poll.forms import RuleForm2,CategoryForm

TAG_CLASSES = ['tag14', 'tag13', 'tag12', 'tag11', 'tag10', 'tag9', 'tag8', 'tag7', 'tag6', 'tag5', 'tag4', 'tag3',
               'tag2', 'tag1']


def generate_tag_cloud(
        words,
        counts_dict,
        tag_classes
):
    """
        returns tag words with assosiated tag classes depending on their frequency
    @params:
             words: a dictionary of words and their associated counts
             counts_dict: a dictionary of counts and their associated words
             tag_classes: a list of tag classes sorted minumum to max
            """
    tags = []
    used_words_list = []
    divisor = tag_cloud_size / len(tag_classes) + 1
    c_keys = counts_dict.keys()
    c_keys.sort()
    c_keys.reverse()
    for count in c_keys:
        for word in counts_dict[count]:
            if not word in used_words_list:
                k = {}
                klass = tag_classes[len(tags) / divisor]

                #url reverse hates single quotes. turn to double quotes
                k['tag'] = "%s" % word
                k['class'] = klass
                tags.append(k)
                used_words_list.append(word)
                if len(used_words_list) == tag_cloud_size:
                    return tags

    return tags


def dictinvert(dict):
    inv = {}
    for k, v in dict.iteritems():
        keys = inv.setdefault(v, [])
        keys.append(k)
    return inv


@login_required
def add_drop_word(request, tag_name=None, poll_pk=None):
    IgnoredTags.objects.create(name=tag_name,
                               poll=get_object_or_404(Poll,
                                                      pk=int(poll_pk)))
    return HttpResponse(simplejson.dumps('success'))


@login_required
def delete_drop_word(request, tag_pk):
    tag = get_object_or_404(IgnoredTags, pk=int(tag_pk))
    tag.delete()
    return HttpResponse(simplejson.dumps('success'))


@login_required
@cache_control(no_cache=True, max_age=0)
def show_ignored_tags(request, poll_id):
    tags = IgnoredTags.objects.filter(poll__pk=poll_id)
    return render_to_response('ureport/partials/tag_cloud/ignored_tags.html'
                              , {'tags': tags, 'poll_id': poll_id},
                              context_instance=RequestContext(request))


def _get_tags(polls):
    words = ''
    word_count = {}
    counts_dict = {}
    used_words_list = []
    drops = "'" + "','".join(drop_words) + "'"
    if type(polls) == types.ListType:
        p_list = [poll.pk for poll in polls]
        poll_pks = str(Poll.objects.filter(pk__in=p_list).values_list('pk', flat=True))[1:-1]
    else:
        poll_pks = str(polls.values_list('pk', flat=True))[1:-1]
    sql = """  SELECT
           (regexp_matches(lower(word),E'[a-zA-Z]+'))[1] as wo,
           count(*) as c
        FROM
           (SELECT
              regexp_split_to_table("rapidsms_httprouter_message"."text",
              E'\\\\s+') as word
           from
              "rapidsms_httprouter_message"
           JOIN
              "poll_response"
                 ON "poll_response"."message_id"= "rapidsms_httprouter_message"."id"
           where
              poll_id in (%(polls)s)
              and has_errors='f')t
        WHERE
           NOT (word in (SELECT
              "ureport_ignoredtags"."name"
           FROM
              "ureport_ignoredtags"
           WHERE
              "ureport_ignoredtags"."poll_id" in (%(polls)s)))

        GROUP BY
           wo
        order by
           c DESC limit 200;   """ % {'polls': poll_pks}
    # poll question

    poll_qn = 'Qn:' + ' '.join(textwrap.wrap(polls[0].question.rsplit('?'
    )[0])) + '?'

    print sql
    cursor = connection.cursor()
    cursor.execute(sql)
    rows = cursor.fetchall()
    rows_dict = dict(rows)
    bl = list(IgnoredTags.objects.filter(poll__in=polls).values_list("name", flat=True))
    for key in rows_dict.keys():
        if len(key) > 2 and not key in drop_words + bl:
            word_count[str(key)] = int(rows_dict[key])

    #gen inverted dictionary
    counts_dict = dictinvert(word_count)

    tags = generate_tag_cloud(word_count, counts_dict, TAG_CLASSES)

    # randomly shuffle tags

    random.shuffle(tags)
    print counts_dict
    return tags


@cache_control(no_cache=True, max_age=0)
def tag_cloud(request, pks):
    """
        generates a tag cloud
    """

    polls = retrieve_poll(request, pks)

    poll_qn = ['Qn:' + ' '.join(textwrap.wrap(poll.question.rsplit('?'
    )[0])) + '?' for poll in polls]

    tags = _get_tags(polls)
    return render_to_response('ureport/partials/tag_cloud/tag_cloud.html'
                              , {
            'poll': polls[0],
            'tags': tags,
            'poll_qn': poll_qn[0],
            'poll_id': pks,
            }, context_instance=RequestContext(request))


def histogram(request, pks=None):
    """
         view for numeric polls
    """

    all_polls = Poll.objects.filter(type=u'n')
    pks = (pks if pks != None else request.GET.get('pks', None))
    if pks:
        items = 6
        polls = retrieve_poll(request, pks)
        responses = Response.objects.filter(poll__in=polls)
        pks = polls.values_list('pk', flat=True)
        responses = Response.objects.filter(poll__in=polls,
                                            poll__type=u'n')
        plottable_data = {}
        if responses:
            poll_results = {}
            poll_qns = ['Qn:' + poll.question + '<br>' for poll in
                        Poll.objects.filter(pk__in=pks)]

            total_responses = responses.count()
            vals_list =\
            Value.objects.filter(entity_id__in=responses).values_list('value_float'
                                                                      , flat=True)
            vals_list = sorted(vals_list)
            max = int(vals_list[-1])
            min = int(vals_list[0])
            num_list = range(min, max)
            increment = int(max / items)
            bounds = num_list[::increment]
            ranges_list = [str(a) + '-' + str(a + increment) for a in
                           bounds if a < max]
            poll_results['categories'] = ranges_list
            poll_results['title'] = poll_qns

            for response in responses:
                name = response.poll.name
                poll_results.setdefault(name, {})
                poll_results[name].setdefault('data', {})
                if len(response.eav_values.all()) > 0:
                    value =\
                    int(response.eav_values.all()[0].value_float)
                pos = bisect.bisect_right(bounds, value) - 1
                r = ranges_list[pos]
                poll_results[name]['data'].setdefault(r, 0)
                poll_results[name]['data'][r] += 1

            data = []
            for key in poll_results.keys():
                if key not in ['categories', 'title']:
                    d = {}
                    d['name'] = key
                    d['data'] = poll_results[key]['data'].values()
                    data.append(d)
            plottable_data['data'] = data
            plottable_data['title'] = poll_qns
            plottable_data['categories'] = ranges_list
            plottable_data['mean'] = sum(vals_list) / len(vals_list)
            plottable_data['median'] = vals_list[len(vals_list) / 2]
        return HttpResponse(mark_safe(simplejson.dumps(plottable_data)))

    return render_to_response('ureport/histogram.html', {'polls'
                                                         : all_polls},
                              context_instance=RequestContext(request))


def show_timeseries(request, pks):
    polls = retrieve_poll(request, pks)
    poll_obj = polls[0]
    responses = Response.objects.filter(poll=poll_obj)
    start_date = poll_obj.start_date
    end_date = poll_obj.end_date or datetime.datetime.now()
    poll = poll_obj.question.replace('"', '\\"')
    interval = datetime.timedelta(minutes=60)
    current_date = start_date
    message_count_list = []
    while current_date < end_date:
        count = responses.filter(message__date__range=(start_date,
                                                       current_date)).count()
        message_count_list.append(count)
        current_date += interval

    return render_to_response('ureport/partials/viz/timeseries.html', {
        'counts': mark_safe(message_count_list),
        'start': start_date,
        'end': end_date,
        'poll': mark_safe(poll),
        }, context_instance=RequestContext(request))


@login_required
def deleteReporter(request, reporter_pk):
    reporter = get_object_or_404(Contact, pk=reporter_pk)
    if request.method == 'POST':
        reporter.delete()
    return HttpResponse(status=200)


@login_required
def editReporter(request, reporter_pk):
    reporter = get_object_or_404(Contact, pk=reporter_pk)
    reporter_form = EditReporterForm(instance=reporter)
    if request.method == 'POST':
        reporter_form = EditReporterForm(instance=reporter,
                                         data=request.POST)
        if reporter_form.is_valid():
            reporter_form.save()
        else:
            return render_to_response('ureport/partials/contacts/edit_reporter.html'
                                      , {'reporter_form': reporter_form, 'reporter'
                : reporter},
                                      context_instance=RequestContext(request))
        return render_to_response('/ureport/partials/contacts/contacts_row.html'
                                  , {'object'
                                     : Contact.objects.get(pk=reporter_pk),
                                     'selectable': True},
                                  context_instance=RequestContext(request))
    else:
        return render_to_response('ureport/partials/contacts/edit_reporter.html'
                                  , {'reporter_form': reporter_form,
                                     'reporter': reporter},
                                  context_instance=RequestContext(request))


@login_required
def view_responses(req, poll_id):
    poll = get_object_or_404(Poll, pk=poll_id)

    script_polls =\
    ScriptStep.objects.exclude(poll=None).values_list('poll',
                                                      flat=True)
    response_rates = {}
    if poll.pk in script_polls:
        responses = poll.responses.order_by('-date')
    else:
        if hasattr(Contact, 'groups'):
            responses =\
            poll.responses.filter(contact__groups__in=req.user.groups.all()).distinct()
        else:
            responses = poll.responses.all()
        responses = responses.order_by('-date')

        for group in req.user.groups.all():
            try:
                contact_count =\
                poll.contacts.filter(groups__in=[group]).distinct().count()
                response_count =\
                poll.responses.filter(contact__groups__in=[group]).distinct().count()
                response_rates[str(group.name)] = [contact_count]
                response_rates[str(group.name)].append(response_count)
                response_rates[str(group.name)].append(response_count
                                                       * 100.0 / contact_count)
            except ZeroDivisionError:
                response_rates.pop(group.name)
    typedef = Poll.TYPE_CHOICES[poll.type]
    print typedef
    columns = [('Sender', False, 'sender', None)]
    for (column, style_class, sortable, db_field, sorter) in\
    typedef['report_columns']:
        columns.append((column, sortable, db_field, sorter))

    return generic(
        req,
        model=Response,
        response_rates=response_rates,
        queryset=responses,
        objects_per_page=25,
        selectable=True,
        partial_base='ureport/partials/polls/poll_partial_base.html',
        base_template='ureport/responses_base.html',
        paginator_template='ureport/partials/pagination.html',
        row_base=typedef['view_template'],
        action_forms=[AssignToPollForm, AssignResponseGroupForm, ReplyTextForm,
                      DeleteSelectedForm],
        filter_forms=[SearchResponsesForm],
        columns=columns,
        partial_row='ureport/partials/polls/response_row.html',
        )


def _get_responses(poll):
    bad_words = getattr(settings, 'BAD_WORDS', [])
    responses = Response.objects.filter(poll=poll)
    for helldamn in bad_words:
        responses = responses.exclude(message__text__icontains=' %s '
        % helldamn).exclude(message__text__istartswith='%s '
        % helldamn)
    paginator = Paginator(responses, 8)
    responses = paginator.page(1).object_list
    return responses


def best_visualization(request, poll_id=None):
    module = False
    if 'module' in request.GET:
        module = True
    polls = retrieve_poll(request, poll_id)
    poll = polls[0]

    #    if poll_id:
    #        poll = Poll.objects.get(pk=poll_id)
    #    else:
    #        poll = Poll.objects.latest('start_date')
    rate=poll.responses.count()*100/poll.contacts.count()
    dict = {
        'poll': poll,
        'polls': [poll],
        'unlabeled': True,
        'module': module,
        'rate':int(rate),
        }
    if poll.type == Poll.TYPE_TEXT\
    and ResponseCategory.objects.filter(response__poll=poll).count()\
    == 0:
        dict.update({'tags': _get_tags(polls), 'responses'
        : _get_responses(poll), 'poll_id': poll.pk})
    return render_to_response('/ureport/partials/viz/best_visualization.html'
                              , dict,
                              context_instance=RequestContext(request))


def ureport_content(
        request,
        slug,
        base_template='ureport/two-column.html',
        **kwargs
):
    createpage = kwargs.setdefault('create', False)
    if not createpage:
        reporter = get_object_or_404(Dashboard, slug=slug, user=None)
    return generic_dashboard(
        request,
        slug=slug,
        module_types=[('ureport', PollModuleForm,
                       'uReport Visualizations'), ('static',
                                                   StaticModuleForm, 'Static Content')],
        base_template=base_template,
        title=None,
        **kwargs
    )


def message_feed(request, pks):
    polls = retrieve_poll(request, pks)
    poll = polls[0]
    return render_to_response('/ureport/partials/viz/message_feed.html'
                              , {'poll': poll, 'responses'
        : _get_responses(poll)},
                              context_instance=RequestContext(request))


@cache_control(no_cache=True, max_age=0)
def poll_summary(request):
    script_polls =\
    ScriptStep.objects.exclude(poll=None).values_list('poll',
                                                      flat=True)
    polls =\
    Poll.objects.exclude(pk__in=script_polls).order_by('-start_date'
    )
    return render_to_response('/ureport/poll_summary.html', {'polls'
                                                             : polls, 'poll': polls[0]},
                              context_instance=RequestContext(request))


def get_all_contacts(request):
    from uganda_common.utils import ExcelResponse

    contacts = Contact.objects.all()
    export_data_list = []
    for contact in contacts:
        if contact.name:
            export_data = SortedDict()
            export_data['name'] = contact.name
            if contact.gender:
                export_data['sex'] = contact.gender
            else:
                export_data['sex'] = 'N/A'
            if contact.birthdate:
                try:
                    contact.birthdate.tzinfo = None
                    export_data['age'] = (datetime.datetime.now()
                                          - contact.birthdate).days / 365
                except:
                    continue
            else:
                export_data['age'] = 'N/A'
            if contact.reporting_location:
                export_data['district'] =\
                contact.reporting_location.name
            else:
                export_data['district'] = 'N/A'
            if contact.village:
                export_data['village'] = contact.village.name
            else:
                export_data['village'] = 'N/A'
            if contact.groups.count() > 0:
                export_data['group'] = contact.groups.all()[0].name
            else:
                export_data['group'] = 'N/A'

            export_data_list.append(export_data)

    response = ExcelResponse(export_data_list)
    return response


def bulk_upload_contacts(request):
    """
    bulk upload contacts from an excel file
    """

    if request.method == 'POST':
        contactsform = ExcelUploadForm(request.POST, request.FILES)
        if contactsform.is_valid():
            if contactsform.is_valid()\
            and request.FILES.get('excel_file', None):
                fields = [
                    'telephone number',
                    'name',
                    'district',
                    'county',
                    'village',
                    'age',
                    'gender',
                    ]
                message = handle_excel_file(request.FILES['excel_file'
                                            ], contactsform.cleaned_data['assign_to_group'
                ], fields)
            return render_to_response('ureport/bulk_contact_upload.html'
                                      , {'contactsform': contactsform, 'message'
                : message},
                                      context_instance=RequestContext(request))

    contactsform = ExcelUploadForm()
    return render_to_response('ureport/bulk_contact_upload.html',
            {'contactsform': contactsform},
                              context_instance=RequestContext(request))


def handle_excel_file(file, group, fields):
    if file:
        excel = file.read()
        workbook = open_workbook(file_contents=excel)
        worksheet = workbook.sheet_by_index(0)
        cols = parse_header_row(worksheet, fields)
        contacts = []
        duplicates = []
        invalid = []
        info = ''

        if not group:
            default_group =\
            Group.objects.filter(name__icontains='ureporters')[0]
            group = default_group

        if worksheet.nrows > 1:
            validated_numbers = []
            for row in range(1, worksheet.nrows):
                numbers = parse_telephone(row, worksheet, cols)
                for raw_num in numbers.split('/'):
                    if raw_num[-2:] == '.0':
                        raw_num = raw_num[:-2]
                    if raw_num[:1] == '+':
                        raw_num = raw_num[1:]
                    if len(raw_num) >= 9:
                        validated_numbers.append(raw_num)
            duplicates =\
            Connection.objects.filter(identity__in=validated_numbers).values_list('identity'
                                                                                  , flat=True)

            for row in range(1, worksheet.nrows):
                numbers = parse_telephone(row, worksheet, cols)
                if len(numbers) > 0:
                    contact = {}
                    contact['name'] = parse_name(row, worksheet, cols)
                    district = (parse_district(row, worksheet,
                                               cols) if 'district' in fields else None)
                    village = (parse_village(row, worksheet,
                                             cols) if 'village' in fields else None)
                    birthdate = (parse_birthdate(row, worksheet,
                                                 cols) if 'age' in fields else None)
                    gender = (parse_gender(row, worksheet,
                                           cols) if 'gender' in fields else None)
                    if district:
                        contact['reporting_location'] =\
                        find_closest_match(district,
                                           Location.objects.filter(type__slug='district'
                                           ))
                    if village:
                        contact['village'] =\
                        find_closest_match(village,
                                           Location.objects)
                    if birthdate:
                        contact['birthdate'] = birthdate
                    if gender:
                        contact['gender'] = gender
                    if group:
                        contact['groups'] = group

                    for raw_num in numbers.split('/'):
                        if raw_num[-2:] == '.0':
                            raw_num = raw_num[:-2]
                        if raw_num[:1] == '+':
                            raw_num = raw_num[1:]
                        if len(raw_num) >= 9:
                            if raw_num not in duplicates:
                                (number, backend) =\
                                assign_backend(raw_num)
                                if number not in contacts and backend\
                                is not None:
                                    Connection.bulk.bulk_insert(send_pre_save=False,
                                                                identity=number, backend=backend, contact=contact)
                                    contacts.append(number)
                                elif backend is None:
                                    invalid.append(raw_num)
                        else:
                            invalid.append(raw_num)

            connections =\
            Connection.bulk.bulk_insert_commit(send_post_save=False,
                                               autoclobber=True)
            contact_pks = connections.values_list('contact__pk',
                                                  flat=True)

            if len(contacts) > 0:
                info = 'Contacts with numbers... '\
                       + ' ,'.join(contacts)\
                + ''' have been uploaded !

'''
            if len(duplicates) > 0:
                info = info\
                       + 'The following numbers already exist in the system and thus have not been uploaded: '\
                       + ' ,'.join(duplicates) + '''

'''
            if len(invalid) > 0:
                info = info\
                       + 'The following numbers may be invalid and thus have not been added to the system: '\
                       + ' ,'.join(invalid) + '''

'''
        else:
            info =\
            'You seem to have uploaded an empty excel file, please fill the excel Contacts Template with contacts and upload again...'
    else:
        info = 'Invalid file'
    return info


def parse_header_row(worksheet, fields):

#    fields=['telephone number','name', 'district', 'county', 'village', 'age', 'gender']

    field_cols = {}
    for col in range(worksheet.ncols):
        value = str(worksheet.cell(0, col).value).strip()
        if value.lower() in fields:
            field_cols[value.lower()] = col
    return field_cols


def parse_telephone(row, worksheet, cols):
    try:
        number = str(worksheet.cell(row, cols['telephone number'
        ]).value)
    except KeyError:
        number = str(worksheet.cell(row, cols['telephone']).value)
    return number.replace('-', '').strip().replace(' ', '')


def parse_name(row, worksheet, cols):
    try:
        name = str(worksheet.cell(row, cols['company name'
        ]).value).strip()
    except KeyError:
        name = str(worksheet.cell(row, cols['name']).value).strip()
    if name.__len__() > 0:

    #        name = str(worksheet.cell(row, cols['name']).value)

        return ' '.join([t.capitalize() for t in name.lower().split()])
    else:
        return 'Anonymous User'


def parse_district(row, worksheet, cols):
    return str(worksheet.cell(row, cols['district']).value)


def parse_village(row, worksheet, cols):
    return str(worksheet.cell(row, cols['village']).value)


def parse_birthdate(row, worksheet, cols):
    try:
        age = int(worksheet.cell(row, cols['age']).value)
        birthdate = '%d/%d/%d' % (datetime.datetime.now().day,
                                  datetime.datetime.now().month,
                                  datetime.datetime.now().year - age)
        return datetime.datetime.strptime(birthdate.strip(), '%d/%m/%Y')
    except ValueError:
        return None


def parse_gender(row, worksheet, cols):
    gender = str(worksheet.cell(row, cols['gender']).value)
    return (gender.upper()[:1] if gender else None)


def download_contacts_template(request, f):
    path = getattr(settings, 'DOWNLOADS_FOLDER', None)
    fh = open(path + f)
    data = File(fh).read()
    response = HttpResponse(data, mimetype='application/vnd.ms-excel')
    response['Content-Disposition'] = 'attachment; filename=' + f
    return response


def clickatell_wrapper(request):
    request.GET = request.GET.copy()
    request.GET.update({'backend': 'clickatell', 'sender'
    : request.GET['from'], 'message'
    : request.GET['text']})
    return receive(request)

@login_required
def flagged_messages(request):
    all_flags = Flag.objects.all()
    if request.GET.get('export', None):
        flaggedmessages = MessageFlag.objects.exclude(flag=None)

        data = []
        for mf in flaggedmessages:
            rep = {}

            rep['Message'] = mf.message.text
            rep['Mobile Number'] = mf.message.connection.identity
            rep['flag'] = mf.flag.name
            rep['date']=mf.message.date.date()
            if mf.message.connection.contact:
                rep['name'] = mf.message.connection.contact.name
                rep['district'] = mf.message.connection.contact.reporting_location
            else:
                rep['name'] = ''
                rep['district'] = ''

            data.append(rep)

        return ExcelResponse(data=data)
    return generic(
        request,
        model=MessageFlag,
        queryset=get_flagged_messages,
        objects_per_page=10,
        results_title='Flagged Messages',
        selectable=False,
        partial_row='ureport/partials/messages/flagged_message_row.html'
        ,
        base_template='ureport/flagged_message_base.html',
        columns=[('Message', True, 'message__text', SimpleSorter()),
            ('Sender Information', True,
             'message__connection__contact__name', SimpleSorter()),
            ('Date', True, 'message__date', SimpleSorter()),
            ('Flags', False, 'message__flagged', None)],
        sort_column='date',
        sort_ascending=False,
        all_flags=all_flags

    )

@login_required
def view_flagged_with(request, pk):
    flag = get_object_or_404(Flag, pk=pk)
    messages = flag.get_messages()
    if request.GET.get('export', None):
        data = []
        for message in messages:
            rep = {}

            rep['Message'] = message.text
            rep['Mobile Number'] = message.connection.identity
            rep['flag'] = flag.name
            if message.connection.contact:
                rep['name'] = message.connection.contact.name
                rep['district'] = message.connection.contact.reporting_location
            else:
                rep['name'] = ''
                rep['district'] = ''
            data.append(rep)

        return ExcelResponse(data=data)
    return generic(
        request,
        model=Message,
        queryset=messages,
        objects_per_page=25,
        partial_row='contact/partials/message_row.html',
        base_template='ureport/view_flagged_with_base.html',
        results_title='Messages Flagged With %s' % flag.name,
        columns=[('Message', True, 'text', SimpleSorter()),
            ('Sender Information', True,
             'connection__contact__name', SimpleSorter()), ('Date',
                                                            True, 'date', SimpleSorter()), ('Type', True,
                                                                                            'application',
                                                                                            SimpleSorter())],
        sort_column='date',
        sort_ascending=False,
        )

@login_required
def create_flags(request, pk=None):
    all_flags = Flag.objects.all()
    flag = Flag()
    if pk:
        try:
            flag = Flag.objects.get(pk=int(pk))
        except Flag.DoesNotExist:
            flag = Flag()

    if request.method == 'POST':
        flags_form = FlaggedMessageForm(request.POST, instance=flag)
        if flags_form.is_valid():
            flags_form.save()
            return HttpResponseRedirect('/flaggedmessages')
    else:
        flags_form = FlaggedMessageForm(instance=flag)

    return render_to_response('ureport/new_flag.html',
                              dict(flags_form=flags_form,
                                   all_flags=all_flags),
                              context_instance=RequestContext(request))

@login_required
def delete_flag(request, flag_pk):
    flag = get_object_or_404(Flag, pk=flag_pk)
    if flag:
        flag.delete()
        return HttpResponse('Success')
    else:
        return HttpResponse('Failed')


def signup(request):
    status_message = None
    if request.method == 'POST':
        signup_form = SignupForm(request.POST)
        if signup_form.is_valid():
            mobile = signup_form.cleaned_data['mobile']
            (number, backend) = assign_backend(mobile)

            # create our connection

            (connection, created) =\
            Connection.objects.get_or_create(backend=backend,
                                             identity=number)
            connection.contact =\
            Contact.objects.create(name=signup_form.cleaned_data['firstname'
                                        ] + ' ' + signup_form.cleaned_data['lastname'])
            connection.contact.reporting_location =\
            signup_form.cleaned_data['district']
            connection.contact.gender =\
            signup_form.cleaned_data['gender']
            connection.contact.village =\
            find_closest_match(signup_form.cleaned_data['village'],
                               Location.objects)
            connection.contact.birthdate = datetime.datetime.now()\
            - datetime.timedelta(days=365
            * int(signup_form.cleaned_data['age']))

            group_to_match = signup_form.cleaned_data['group']

            if Group.objects.filter(name='Other uReporters').count():
                default_group =\
                Group.objects.get(name='Other uReporters')
                connection.contact.groups.add(default_group)
            if group_to_match:
                for g in re.findall(r'\w+', group_to_match):
                    if g:
                        group = find_closest_match(str(g),
                                                   Group.objects)
                        if group:
                            connection.contact.groups.add(group)
                            break

            connection.save()
            status_message = 'You have successfully signed up :)'
            Message.objects.create(date=datetime.datetime.now(),
                                   connection=connection, direction='O'
                                   , status='Q',
                                   text='CONGRATULATIONS!!! You are now a registered member of Ureport! With Ureport, you can make a real difference!  Speak Up and Be Heard! from UNICEF'
            )
        else:
            return render_to_response('ureport/signup.html',
                                      dict(signup_form=signup_form),
                                      context_instance=RequestContext(request))
    signup_form = SignupForm()
    return render_to_response('ureport/signup.html',
                              dict(signup_form=signup_form,
                                   status_message=status_message),
                              context_instance=RequestContext(request))


@login_required
def ureporter_profile(request, connection_pk):
    from script.models import ScriptSession, ScriptResponse

    connection = get_object_or_404(Connection, pk=connection_pk)
    session = ScriptSession.objects.filter(connection__pk=connection_pk)

    messages =\
    Message.objects.filter(connection=connection).order_by('-date')

    contact = connection.contact
    total_outgoing = messages.filter(direction='O',
                                     connection__pk=connection_pk).count()
    total_incoming = messages.filter(direction='I',
                                     connection__pk=connection_pk).count()
    try:
        response_rate = contact.responses.values_list('poll'
        ).distinct().count() * 100\
        / float(Poll.objects.filter(contacts=contact).distinct().count())
    except (ZeroDivisionError, ValueError):
        response_rate = None
    gr_poll = Poll.objects.get(pk=121)
    how_did_u_hear = None
    if session.exists():
        try:
            how_did_u_hear =\
            session[0].responses.filter(response__poll=gr_poll)[0].response.message.text
        except (ScriptResponse.DoesNotExist, IndexError):
            how_did_u_hear = 'N/A'
    if request.GET.get('download',None):

        data = []
        for message in messages:
            rep = {}

            rep['Message'] = message.text
            rep['direction']=message.direction
            rep['date']=message.date.date()
            rep['Mobile Number'] = message.connection.identity


            if message.connection.contact:
                rep['name'] = message.connection.contact.name
                rep['district'] = message.connection.contact.reporting_location
            else:
                rep['name'] = ''
                rep['district'] = ''
            data.append(rep)

        return ExcelResponse(data=data)
    columns = [('Message', True, 'text', SimpleSorter()), ('connection'
                                                           , True, 'connection', SimpleSorter()), ('Date', True,
                                                                                                   'date',
                                                                                                   SimpleSorter()),
        ('Direction', True, 'direction'
         , SimpleSorter())]

    # hack hack send the reply message by hacking the sendmessage form
    if request.method == 'POST':
        if not request.POST.get('text', None) == u''\
        and request.POST.get('action')\
        == u'ureport.forms.ReplyTextForm':
            Message.objects.create(date=datetime.datetime.now(),
                                   connection=connection, direction='O'
                                   , status='Q',
                                   text=request.POST.get('text'))
            return generic(
                request,
                model=Message,
                queryset=messages,
                total_outgoing=total_outgoing,
                total_incoming=total_incoming,
                response_rate=response_rate,
                how_did_u_hear=how_did_u_hear,
                contact=contact,
                objects_per_page=20,
                status_message='Message sent',
                status_message_type='success',
                results_title='Message History',
                selectable=False,
                partial_row='ureport/partials/messages/message_history_row.html'
                ,
                base_template='ureport/message_history_base.html',
                action_forms=[ReplyTextForm],
                columns=columns,
                sort_column='date',
                sort_ascending=False,
                )

    return generic(
        request,
        model=Message,
        queryset=messages,
        contact=contact,
        total_outgoing=total_outgoing,
        total_incoming=total_incoming,
        response_rate=response_rate,
        objects_per_page=20,
        how_did_u_hear=how_did_u_hear,
        results_title='Message History',
        selectable=False,
        partial_row='ureport/partials/messages/message_history_row.html'
        ,
        base_template='ureport/message_history_base.html',
        action_forms=[ReplyTextForm],
        columns=columns,
        sort_column='date',
        sort_ascending=False,
        )


@permission_required('poll.can_poll')
def new_poll(req):
    if req.method == 'POST':
        form = NewPollForm(req.POST)
        form.updateTypes()
        if form.is_valid():
            # create our XForm
            question = form.cleaned_data['question']
            default_response = form.cleaned_data['default_response']
            districts = form.cleaned_data['districts']
            if hasattr(Contact, 'groups'):
                groups = form.cleaned_data['groups']

            if len(districts):
                contacts = Contact.objects.filter(reporting_location__in=districts).filter(groups__in=groups).distinct()
            else:
                contacts = Contact.objects.filter(groups__in=groups).distinct()

            name = form.cleaned_data['name']
            p_type = form.cleaned_data['type']
            response_type = form.cleaned_data['response_type']
            if not form.cleaned_data['default_response_luo'] == ''\
            and not form.cleaned_data['default_response'] == '':
                (translation, created) =\
                Translation.objects.get_or_create(language='ach',
                                                  field=form.cleaned_data['default_response'],
                                                  value=form.cleaned_data['default_response_luo'])

            if not form.cleaned_data['question_luo'] == '':
                (translation, created) =\
                Translation.objects.get_or_create(language='ach',
                                                  field=form.cleaned_data['question'],
                                                  value=form.cleaned_data['question_luo'])

            poll_type = (Poll.TYPE_TEXT if p_type
            == NewPollForm.TYPE_YES_NO else p_type)

            poll = Poll.create_with_bulk(\
                name,
                poll_type,
                question,
                default_response,
                contacts,
                req.user)

            if p_type == NewPollForm.TYPE_YES_NO:
                poll.add_yesno_categories()

            if settings.SITE_ID:
                poll.sites.add(Site.objects.get_current())

            return redirect(reverse('poll.views.view_poll', args=[poll.pk]))

    else:
        form = NewPollForm()
        form.updateTypes()

    return render_to_response('ureport/new_poll.html', {'form': form},
                              context_instance=RequestContext(req))


@login_required
def mp_dashboard(request):
    from contact.forms import FilterGroupsForm, MultipleDistictFilterForm, GenderFilterForm, AgeFilterForm
    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

    groupform = AssignResponseGroupForm(request=request)
    if request.method == "POST" and request.POST.get('groups', None):
        g_form = AssignResponseGroupForm(request.POST, request=request)
        if g_form.is_valid():
            request.session['groups'] = g_form.cleaned_data['groups']
    if not request.session.get('groups', None):
        mp_contacts = Contact.objects.filter(groups__name__in=['MP'])
    else:
        mp_contacts = Contact.objects.filter(groups__in=request.session.get('groups'))
    forms = [MultipleDistictFilterForm, FilterGroupsForm, GenderFilterForm, AgeFilterForm]
    filter_forms = []
    mp_conns = Connection.objects.filter(contact__in=mp_contacts)
    contacts = Contact.objects.exclude(connection__in=Blacklist.objects.all()).distinct()
    message_list =\
    Message.objects.filter(connection__in=mp_conns, direction="I").order_by('-date')
    if request.GET.get("ajax", None):
        date = datetime.datetime.now() - datetime.timedelta(seconds=15)
        msgs = Message.objects.filter(connection__in=mp_conns, direction="I").filter(date__gte=date)
        msgs_list = []
        if msgs.exists():
            for msg in msgs:
                m = {}
                m["text"] = msg.text
                m["date"]=str(msg.date.date())
                m["name"] = msg.connection.contact.name
                m["number"] = msg.connection.identity
                if msg.connection.contact.reporting_location:
                    m["district"] = msg.connection.contact.reporting_location.name
                else:
                    m["district"] = "N/A"

                m["group"] = msg.connection.contact.groups.all()[0].name
                msgs_list.append(m)
            return HttpResponse(mark_safe(simplejson.dumps(msgs_list)))
        else:
            return HttpResponse("success")

    old_contacts = contacts
    if request.POST and request.GET.get("filter", None):
        for form_class in forms:
            form_instance = form_class(request.POST, request=request)
            if form_instance.is_valid():
                contacts = form_instance.filter(request, contacts)
        if old_contacts.count() == contacts.count():
            return HttpResponse("No Contacts Selected")
        else:
            request.session['filtered'] = contacts
            return HttpResponse(str(contacts.count()))
    for form in forms:
        filter_forms.append(form(**{'request': request}))
    paginator = Paginator(message_list, 15)
    page = request.GET.get('page', 1)
    try:
        messages = paginator.page(page)
    except PageNotAnInteger:
        # If page is not an integer, deliver first page.
        messages = paginator.page(1)
    except EmptyPage:
        # If page is out of range (e.g. 9999), deliver last page of results.
        messages = paginator.page(paginator.num_pages)
    poll_form = NewPollForm()
    poll_form.updateTypes()

    if request.method == "POST" and request.GET.get("poll", None):
        res_dict = request.POST.copy()
        res_dict.update({'groups': u'2'})
        poll_form = NewPollForm(res_dict)
        poll_form.updateTypes()
        #create poll
        if request.session.get("filtered", None) and poll_form.is_valid():
            name = poll_form.cleaned_data['name']
            p_type = poll_form.cleaned_data['type']
            response_type = poll_form.cleaned_data['response_type']
            question = poll_form.cleaned_data['question']
            default_response = poll_form.cleaned_data['default_response']

            if not poll_form.cleaned_data['default_response_luo'] == ''\
            and not poll_form.cleaned_data['default_response'] == '':
                (translation, created) =\
                Translation.objects.get_or_create(language='ach',
                                                  field=poll_form.cleaned_data['default_response'],
                                                  value=poll_form.cleaned_data['default_response_luo'])

            if not poll_form.cleaned_data['question_luo'] == '':
                (translation, created) =\
                Translation.objects.get_or_create(language='ach',
                                                  field=poll_form.cleaned_data['question'],
                                                  value=poll_form.cleaned_data['question_luo'])

            poll_type = (Poll.TYPE_TEXT if p_type
            == NewPollForm.TYPE_YES_NO else p_type)

            poll = Poll.create_with_bulk(\
                name,
                poll_type,
                question,
                default_response,
                request.session.get("filtered"),
                request.user)
            return redirect(reverse('poll.views.view_poll', args=[poll.pk]))

    context_dict = {"poll_form": poll_form,
                    "filter_forms": filter_forms,
                    'messages': messages,
                    'groupform': groupform}

    return render_to_response('ureport/mp_dashboard.html', context_dict,
                              context_instance=RequestContext(request))

@login_required
def ussd_manager(request):
    ussd_contacts=Contact.objects.filter(groups__name="equatel")
    ussd_conns=Connection.objects.filter(contact__in=ussd_contacts)
    messages=Message.objects.filter(connection__in=ussd_conns).order_by('-date')



    return generic(
        request,
        model=Message,
        queryset=messages,
        objects_per_page=25,
        partial_row='contact/partials/message_row.html',
        base_template='ureport/ussd_messages_base.html',
        results_title='Ussd Messages',
        columns=[('Message', True, 'text', SimpleSorter()),
            ('Sender Information', True,
             'connection__contact__name', SimpleSorter()), ('Date',
                                                            True, 'date', SimpleSorter()), ('Type', True,
                                                                                            'application',
                                                                                            SimpleSorter())],
        sort_column='date',
        sort_ascending=False,
        )
@login_required
def blacklist(request,pk):
    contact=Contact.objects.get(pk=int(pk))
    if request.user and request.user.has_perm('unregister.add_blacklist'):
        Blacklist.objects.get_or_create(connection=contact.default_connection)
        Message.objects.create(status="Q",direction="O",connection=contact.default_connection,text="Your UReport opt out is confirmed.If you made a mistake,or you want your voice to be heard again,text in JOIN and send it to 8500!All SMS messages are free")
        return HttpResponse(status=200)
@login_required
def view_poll(request,pk):
    if request.GET.get('start'):
        pass
    xf=XFormField.objects.get(name='latest_poll')
    response=StubScreen.objects.get(slug='question_response')
    template='ureport/polls/view_poll.html'
    poll=Poll.objects.get(pk=pk)
    categories=poll.categories.all()
    category_form=CategoryForm()
    rule_form=RuleForm2()
    if request.method == "POST":
        if request.GET.get('edit'):
            if request.POST.get('poll[default_response]'):
                poll.default_response=request.POST['poll[default_response]']
                poll.save()
            if request.POST.get('poll[question]'):
                poll.default_response=request.POST['poll[question]']
                poll.save()

        if request.GET.get("ussd",None):
            question=request.POST.get("question")
            response=request.POST.get("response")
            xf.question=question
            xf.save()
            response.text=response
            response.save()
        if request.GET.get("category",None):
            if request.GET.get('pk'):
                category=Category.objects.get(pk=int(pk))
            else:
                category=Category()
                category.poll=poll
            category_form=CategoryForm(request.POST,instance=category)
            if category_form.is_valid():
                template="ureport/polls/rules.html"
                category_form.save()
                request.session['category'] =category
            else:
                template="ureport/polls/category.html"

        if request.GET.get("rules",None):
            rule=Rule()
            rule.category=request.session['category']
            rule_form=RuleForm2(request.POST,instance=rule)
            if rule_form.is_valid:
                rule_form.save()
            else:
                template="ureport/polls/rules.html"





    return render_to_response(template, {
        'poll': poll,
        'xf':xf,
        'response':response,
        'categories': categories,
        'category_form':category_form,
        'rule_form':rule_form,
        }, context_instance=RequestContext(request))

@login_required
def edit_category(request,pk):
    category=Category.objects.get(pk=int(pk))
    category_form=CategoryForm(instance=category)
    return render_to_response("ureport/polls/category.html",{'category':category,'category_form':category_form,'edit':True},context_instance=RequestContext(request))

@login_required
def view_rules(request,pk):
    category=Category.objects.get(pk=int(pk))
    rules=category.rules.all()
    rule=Rule()
    rule.category=category
    rule_form=RuleForm2(instance=rule)
    return render_to_response("ureport/polls/rules.html",{'rules':rules,'rule_form':rule_form,'category':category,"edit":True},context_instance=RequestContext(request))


@login_required
def alerts(request):
    select_poll=SelectPoll()
    poll_form = NewPollForm()
    range_form=rangeForm()
    poll_form.updateTypes()
    template="ureport/polls/alerts.html"
    message_list=Message.objects.filter(details__attribute__name="alert").order_by('-date')
    capture_status,_=Settings.objects.get_or_create(attribute='alerts')
    rate,_=MessageAttribute.objects.get_or_create(name="rating")
    #message_list=[Message.objects.latest('date')]
    #use more efficient count
    if request.GET.get('download',None):


        data=list(AlertsExport.objects.all().values())
        return ExcelResponse(data=data)
    if request.GET.get('capture',None):
        s,_=Settings.objects.get_or_create(attribute='alerts')
        if s.value=='true':
            s.value='false'
            s.save()
            reply="Start Capture"
        else:
            s.value='true'
            s.save()
            reply="Stop Capture"
        return HttpResponse(reply)
    if request.GET.get("ajax", None):
        date = datetime.datetime.now() - datetime.timedelta(seconds=30)
        prev=request.session.get('prev',[])
        msgs = Message.objects.filter(details__attribute__name="alert", direction="I").filter(date__gte=date).exclude(pk__in=prev)
        request.session['prev']=list(msgs.values_list('pk',flat=True))
        msgs_list = []
        if msgs.exists():
            for msg in msgs:
                from django.template.loader import render_to_string
                row_rendered=render_to_string('ureport/partials/row.html', { 'msg': msg })

                m = {}
                m["text"] = msg.text
                m["date"]=str(msg.date.date())
                if msg.connection.contact:
                    m["name"] = msg.connection.contact.name
                else:
                    m['name']="Anonymous User"
                m["number"] = msg.connection.identity
                if msg.connection.contact and msg.connection.contact.reporting_location:
                    m["district"] = msg.connection.contact.reporting_location.name
                else:
                    m["district"] = "N/A"
                rating=msg.details.filter(attribute__name="alerts")
                if rating.exists():
                    r=rating[0].value
                else:
                    r=0
                m["row"] = row_rendered
                m['connection']=msg.connection.pk
                m['pk']=msg.pk
                msgs_list.append(m)
            return HttpResponse(mark_safe(simplejson.dumps(msgs_list)))
        else:
            return HttpResponse("success")
    if request.GET.get('rating',None):
        rating=request.GET.get('rating')
        descs={'1':'Requires Attention','2':'Moderate','3':'Important','4':'Urgent','5':'Very Urgent'}
        msg=Message.objects.get(pk=int(request.GET.get('msg')))
        rate,_=MessageAttribute.objects.get_or_create(name="rating")
        det=MessageDetail.objects.create(message=msg,attribute=rate,value=rating,description=descs.get(rating,''))
        response ="""<li><a href='javascript:void(0)'  class="rate%s"

                            title="%s">%s</a></li>"""%(rating,descs.get(rating,''),descs.get(rating,''))

        return HttpResponse(mark_safe(response))



    paginator = Paginator(message_list, 15)
    page = request.GET.get('page', 1)
    try:
        messages = paginator.page(page)
    except (PageNotAnInteger,EmptyPage):
        # If page is not an integer, deliver first page.
        messages = paginator.page(1)


    return render_to_response(template,{'messages':messages,'capture_status':capture_status,'rate':rate,"range_form":range_form},context_instance=RequestContext(request))


@login_required
def send_message(request):
    if not request.method =="POST":
        send_message_form=SendMessageForm()

        if request.GET.get('forward',None):
            msg=request.GET.get('msg')

            template="ureport/partials/forward.html"
            message=Message.objects.get(pk=int(msg))
            send_message_form=SendMessageForm(data={'text':message.text,'recipients':''})
            request.session['mesg']=message
        if request.GET.get('reply',None):
            msg=request.GET.get('msg')
            message=Message.objects.get(pk=int(msg))
            send_message_form=SendMessageForm(data={'text':message.text,'recipients':message.connection.identity})
            template="ureport/partials/reply.html"
            request.session['mesg']=message
    else:
        send_message_form=SendMessageForm(request.POST)
        if request.GET.get('forward'):
            status="forwarded"
        else:
            status="replied"
        if send_message_form.is_valid():
            recs=send_message_form.cleaned_data.get('recipients').split(',')
            st,_=MessageAttribute.objects.get_or_create(name=status)
            det,_=MessageDetail.objects.get_or_create(message=request.session['mesg'],attribute=st,value="1",description="replied")
            for r in recs:
                connection=Connection.objects.get(identity=r)
#                rate,_=MessageAttribute.objects.get_or_create(name="forwarded")
#                det,_=MessageDetail.objects.get_or_create(message=message,attribute=rate,value="1",description="forwarded")
                message=Message.objects.create(direction="O",text=send_message_form.cleaned_data.get('text'),status="Q",connection=connection)

            return HttpResponse('Message Sent :)')
        else:
            return HttpResponse("smothing went wrong")

    return render_to_response(template,{'send_message_form':send_message_form},context_instance=RequestContext(request))


def remove_captured(request):
    range_form=rangeForm(request.POST)
    if range_form.is_valid():
        start=range_form.cleaned_data['startdate']
        end=range_form.cleaned_data['enddate']
        message_list=Message.objects.filter(details__attribute__name="alert").filter(date__range=(start,end))
        alert=MessageAttribute.objects.get(name="alert")
        mesg_details=MessageDetail.objects.filter(message__in=message_list,attribute=alert).delete()
        return HttpResponse("success")

    return HttpResponse("Sucessfully deleted")

import httplib2
@login_required
def kannel_status(request):
    conn = httplib2.Http()
    resp, content = conn.request('http://ureport.ug:13000/status', request.method)
    return HttpResponse(content,content_type="text/html")

def set_autoreg_rules(request):
    if request.GET.get('gr',None):
        g=Group.objects.get(pk=request.GET.get('gr'))

        try:
            a_g=AutoregGroupRules.objects.get(group=g)
            return HttpResponse(str(a_g.values))
        except AutoregGroupRules.DoesNotExist:
            return HttpResponse("")

    if request.method == "POST":

        group_form=GroupRules(request.POST)
        if group_form.is_valid():
            group_form.save()
    else:
        group_form=GroupRules()
    return render_to_response('ureport/partials/groups_form.html',{'group_form':group_form},context_instance=RequestContext(request))


