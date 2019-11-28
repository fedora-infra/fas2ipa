#!/usr/bin/env python3

from fedora.client.fas2 import AccountSystem
from getpass import getpass
import python_freeipa
from python_freeipa import Client

try:
    from settings import *
except:
    from settings_default import *

fas = AccountSystem(
    'https://admin.fedoraproject.org/accounts',
    username=fas_user,
    password=fas_pw,
)

ipa = Client(host=ipa_instance, verify_ssl=ipa_ssl)
ipa.login(ipa_user, ipa_pw)

if not skip_groups:
    # Start by creating groups
    fas_groups = fas.send_request(
        '/group/list',
        req_params={'search': group_search},
        auth=True,
        timeout=240
    )

    for group in fas_groups['groups']:
        print(group['name'], end='    ')
        try:
            ipa.group_add(group['name'], description=group['display_name'].strip())
            print('OK')
        except Exception as e:
            print('FAIL')
            print(e)

# Now move on to users
users = fas.send_request(
    '/user/list',
    req_params={'search': user_search},
    auth=True,
    timeout=240
)

for person in users['people']:
    print(person['username'], end='    ')
    name_split = person['human_name'].split(' ')
    first_name = name_split[0]
    last_name = name_split[1] if len(name_split) > 1 else ''
    try:
        try:
            ipa.user_add(
                person['username'],
                first_name,
                last_name,
                person['human_name'],
                home_directory='/home/fedora/%s' % person['username'],
                disabled=person['status'] != 'active',
                # If they haven't synced yet, they must reset their password:
                random_pass=True,
                fasircnick=person['ircnick'],
                faslocale=person['locale'],
                fastimezone=person['timezone'],
                fasgpgkeyid=[person['gpg_keyid']],
            )
        except python_freeipa.exceptions.FreeIPAError as e:
            if e.message == 'user with name "%s" already exists' % person['username']:
                pass
            else:
                raise e
        print('OK')

        for groupname,group in person['group_roles'].items():
            print(person['username'] + ':' + groupname, end='    ')
            try:
                try:
                    ipa.group_add_member(groupname, users=person['username'])
                    print('OK')
                except python_freeipa.exceptions.ValidationError as e:
                    if e.message['member']['user'][0][1] == 'This entry is already a member':
                        print('OK')
                        pass
                    else:
                        raise e
                if group['role_type'] in ['administrator', 'sponsor']:
                    print(person['username'] + ':' + groupname + ' sponsor status', end='    ')
                    try:
                        ipa._request(
                            'group_add_member_manager',
                            groupname,
                            { 'user': [person['username']] })
                        print('OK')
                    except Exception as e:
                        print('FAIL')
                        print(e)
            except Exception as e:
                print('FAIL')
                print(e)
    except Exception as e:
        print('FAIL')
        print(e)
