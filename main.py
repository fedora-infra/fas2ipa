#!/usr/bin/env python3

from fedora.client.fas2 import AccountSystem
from getpass import getpass
import python_freeipa
from python_freeipa import ClientLegacy as Client
import random
import string
import progressbar

try:
    from settings import *
except:
    from settings_default import *

fas = AccountSystem(
    'https://admin.fedoraproject.org/accounts',
    username=fas_user,
    password=fas_pw,
)

instances = []
for instance in ipa_instances:
    ipa = Client(host=instance, verify_ssl=ipa_ssl)
    ipa.login(ipa_user, ipa_pw)
    instances.append(ipa)

groups_added = 0
groups_counter = 0

if not skip_group_creation:
    # Start by creating groups
    fas_groups = fas.send_request(
        '/group/list',
        req_params={'search': group_search},
        auth=True,
        timeout=240
    )

    fas_groups = [g for g in fas_groups['groups'] if g['name'] not in ignore_groups]

    for group in progressbar.progressbar(fas_groups, redirect_stdout=True):
        groups_counter += 1

        # calculate the IRC channel (FAS has 2 fields, freeipa-fas has a single one )
        # if we have an irc channel defined. try to generate the irc:// uri
        # there are a handful of groups that have an IRC server defined (freenode), but
        # no channel, which is kind of useless, so we don't handle that case.
        irc_channel = group.get("irc_channel")
        irc_string = None
        if irc_channel:
            if irc_channel[0] == "#":
                irc_channel = irc_channel[1:]
            irc_network = group.get("irc_network").lower()
            if "gimp" in irc_network:
                irc_string = f'irc://irc.gimp.org/#{irc_channel}'
            elif "oftc" in irc_network:
                irc_string = f'irc://irc.oftc.net/#{irc_channel}'
            else:
                # the remainder of the entries here are either blank or
                # freenode, so we freenode them all.
                irc_string = f'irc://irc.freenode.net/#{irc_channel}'
        
        url = group.get("url")
        if not url:
            url = None
        
        mailing_list = group.get("mailing_list")
        if not mailing_list:
            mailing_list = None
        elif "@" not in mailing_list:
            mailing_list = f'{mailing_list}@lists.fedoraproject.org'

        print(group['name'], end='    ')
        try:
            ipa.group_add(group['name'], description=group['display_name'].strip(),
            fasgroup=True, fasurl=url, fasmailinglist=mailing_list, fasircchannel=irc_string)
            print('OK')
            groups_added += 1
        except Exception as e:
            print('FAIL')
            print(e)

def chunks(data, n):
    return [data[x:x+n] for x in range(0, len(data), n)]


def re_auth(instances):
    print('Re-authenticating')
    for ipa in instances:
        ipa.logout()
        ipa.login(ipa_user, ipa_pw)

def stats():
    print('#######################################################')
    print('')
    print(f'Successfully added {users_added} users.')
    print(f'Successfully edited {users_edited} users.')
    print('')
    print(f'Successfully created {groups_added} groups.')
    print('')
    print(f'Total FAS groups: {groups_counter}. Total groups added in FreeIPA: { groups_added }')
    print(f'Total FAS users: {user_counter}. Total users changed in FreeIPA: { users_added + users_edited }')
    print('')
    print('#######################################################')


user_counter = 0

users_added = 0
users_edited = 0

alphabet = dict.fromkeys(string.ascii_lowercase, 0)

for letter in alphabet:
    search_string = letter + '*'
    groups_to_member_usernames = {}
    groups_to_sponsor_usernames = {}
    print(f'finding users matching {letter}*')
    users = fas.send_request(
            '/user/list',
            req_params={'search': search_string},
            auth=True,
            timeout=240
            
    )
    people_count = len(users['people'])
    print(f'{people_count} found')

    for person in progressbar.progressbar(users['people'], redirect_stdout=True):
        user_counter += 1
        if user_counter % reauth_every == 0:
            re_auth(instances)
        ipa = random.choice(instances)
        print(person['username'], end='    ')
        if person['human_name']:
            name = person['human_name'].strip()
            name_split = name.split(' ')
            if len(name_split) > 2 or len(name_split) == 1:
                first_name = '<fnu>'
                last_name = name
                display_name = name
                initials = ''
            else:
                first_name = name_split[0].strip()
                last_name = name_split[1].strip()
        else:
            name = '<fnu> <lnu>'
            first_name = '<fnu>'
            last_name = '<lnu>'
        try:
            if not only_map_groups:
                try:
                    ipa.user_add(
                        person['username'],
                        first_name=first_name,
                        last_name=last_name,
                        full_name=name,
                        gecos=name,
                        display_name=display_name,
                        home_directory='/home/fedora/%s' % person['username'],
                        disabled=person['status'] != 'active',
                        # If they haven't synced yet, they must reset their password:
                        random_pass=True,
                        fasircnick=person['ircnick'].strip() if person['ircnick'] else None,
                        faslocale=person['locale'].strip() if person['locale'] else None,
                        fastimezone=person['timezone'].strip() if person['timezone'] else None,
                        fasgpgkeyid=[person['gpg_keyid'][:16].strip() if person['gpg_keyid'] else None],

                    )
                    print('ADDED')
                    users_added += 1
                except python_freeipa.exceptions.FreeIPAError as e:
                    if e.message == 'user with name "%s" already exists' % person['username']:
                        # Update them instead
                        ipa.user_mod(
                            person['username'],
                            first_name=first_name,
                            last_name=last_name,
                            full_name=name,
                            gecos=name,
                            display_name=display_name,
                            home_directory='/home/fedora/%s' % person['username'],
                            disabled=person['status'] != 'active',
                            # If they haven't synced yet, they must reset their password:
                            random_pass=True,
                            fasircnick=person['ircnick'].strip() if person['ircnick'] else None,
                            faslocale=person['locale'].strip() if person['locale'] else None,
                            fastimezone=person['timezone'].strip() if person['timezone'] else None,
                            fasgpgkeyid=[person['gpg_keyid'][:16].strip() if person['gpg_keyid'] else None],
                        )
                        print('UPDATED')
                        users_edited += 1
                    else:
                        raise e

            for groupname, group in person['group_roles'].items():
                if groupname in groups_to_member_usernames:
                    groups_to_member_usernames[groupname].append(person['username'])
                else:
                    groups_to_member_usernames[groupname] = [person['username']]

                if group['role_type'] in ['administrator', 'sponsor']:
                    if groupname in groups_to_sponsor_usernames:
                        groups_to_sponsor_usernames[groupname].append(person['username'])
                    else:
                        groups_to_sponsor_usernames[groupname] = [person['username']]

        except python_freeipa.exceptions.Unauthorized as e:
            ipa.login(ipa_user, ipa_pw)
            continue
        except Exception as e:
            print('FAIL')
            print(e)

    group_member_counter = 0
    for group, members in groups_to_member_usernames.items():
        if group in ignore_groups:
            continue
        with progressbar.ProgressBar(max_value=len(members), redirect_stdout=True) as bar:
            bar.max_value = len(members)
            group_member_counter = 0
            for chunk in chunks(members, group_chunks):
                group_member_counter += 1
                try:
                    instances[0].group_add_member(group, chunk, no_members=True)
                    print('SUCCESS: Added %s as member to %s' % (chunk, group))
                except python_freeipa.exceptions.ValidationError as e:
                    for msg in e.message['member']['user']:
                        print('NOTICE: Failed to add %s to %s: %s' % (msg[0], group, msg[1]))
                    continue
                finally:
                    bar.update(group_member_counter * len(chunk))

    group_sponsor_counter = 0
    for group, sponsors in groups_to_sponsor_usernames.items():
        if group in ignore_groups:
            continue
        with progressbar.ProgressBar(max_value=len(sponsors), redirect_stdout=True) as bar:
            group_sponsor_counter = 0
            for chunk in chunks(sponsors, group_chunks):
                group_sponsor_counter += 1
                try:
                    instances[0]._request(
                        'group_add_member_manager',
                        group,
                        { 'user': chunk })
                    print('SUCCESS: Added %s as sponsor to %s' % (chunk, group))
                except python_freeipa.exceptions.ValidationError as e:
                    for msg in e.message['member']['user']:
                        print('NOTICE: Failed to add %s to %s: %s' % (msg[0], group, msg[1]))
                    continue
                finally:
                    bar.update(group_sponsor_counter * len(chunk))

stats()
