import random
import string

import click
import progressbar
import python_freeipa
import toml
import vcr
from colorama import Fore, Style
from python_freeipa import ClientLegacy as Client
from fedora.client.fas2 import AccountSystem


INPUT_IF_EMTPY = {
    "fas": ["username", "password"],
    "ipa": ["username", "password"],
}


class FASWrapper:
    def __init__(self, config):
        self.fas = AccountSystem(
            "https://admin.fedoraproject.org/accounts",
            username=config["fas"]["username"],
            password=config["fas"]["password"],
        )
        self._replay = config["replay"]
        self._recorder = vcr.VCR(
            ignore_hosts=config["ipa"]["instances"],
            record_mode="new_episodes",
            match_on=["method", "path", "query", "body"],
        )

    def send_request(self, url, *args, **kwargs):
        if not self._replay:
            return self.fas.send_request(url, *args, **kwargs)

        cassette_path = ["fixtures/fas-", url[1:].replace("/", "_"), ".yaml"]
        with self._recorder.use_cassette("".join(cassette_path)):
            return self.fas.send_request(url, *args, **kwargs)


def print_status(text, category):
    if category == "success":
        text = Style.BRIGHT + Fore.GREEN + text
    elif category == "failure":
        text = Style.BRIGHT + Fore.RED + text
    text = text + Style.RESET_ALL
    print(text)


@click.command()
@click.option("--skip-groups", is_flag=True, help="Skip group creation")
@click.option(
    "--only-members",
    is_flag=True,
    help="Only map users/sponsors to groups and ignore updating user entities",
)
def cli(skip_groups, only_members):
    config = toml.load(["config.toml.example", "config.toml"])
    for section, keys in INPUT_IF_EMTPY.items():
        for key in keys:
            if config[section][key]:
                continue
            is_password = key == "password"
            config[section][key] = click.prompt(
                f"Enter {key} for {section}", hide_input=is_password
            )

    fas = FASWrapper(config)
    click.echo("Logged into FAS")

    instances = []
    for instance in config["ipa"]["instances"]:
        ipa = Client(host=instance, verify_ssl=config["ipa"]["cert_path"])
        ipa.login(config["ipa"]["username"], config["ipa"]["password"])
        instances.append(ipa)
    click.echo("Logged into IPA")

    groups_added = 0
    groups_edited = 0
    groups_counter = 0

    if not skip_groups:
        # Start by creating groups
        click.echo("Getting the list of groups...")
        fas_groups = fas.send_request(
            "/group/list",
            req_params={"search": config["group_search"]},
            auth=True,
            timeout=240,
        )

        fas_groups = [
            g for g in fas_groups["groups"] if g["name"] not in config["ignore_groups"]
        ]
        click.echo(f"Got {len(fas_groups)} groups!")

        for group in progressbar.progressbar(fas_groups, redirect_stdout=True):
            groups_counter += 1
            name = group["name"].lower()
            print(name, end="    ")

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
                    irc_string = f"irc://irc.gimp.org/#{irc_channel}"
                elif "oftc" in irc_network:
                    irc_string = f"irc://irc.oftc.net/#{irc_channel}"
                else:
                    # the remainder of the entries here are either blank or
                    # freenode, so we freenode them all.
                    irc_string = f"irc://irc.freenode.net/#{irc_channel}"

            url = group.get("url")
            if not url:
                url = None
            else:
                url = url.strip()

            mailing_list = group.get("mailing_list")
            if not mailing_list:
                mailing_list = None
            else:
                if "@" not in mailing_list:
                    mailing_list = f"{mailing_list}@lists.fedoraproject.org"
                mailing_list = mailing_list.strip()
                mailing_list = mailing_list.rstrip(".")
                mailing_list = mailing_list.lower()

            group_args = dict(
                description=group["display_name"].strip(),
                fasgroup=True,
                fasurl=url,
                fasmailinglist=mailing_list,
                fasircchannel=irc_string,
            )
            try:
                ipa.group_add(name, **group_args)
                print_status("ADDED", "success")
                groups_added += 1
            except python_freeipa.exceptions.FreeIPAError as e:
                if e.message == 'group with name "%s" already exists' % name:
                    try:
                        ipa.group_mod(name, **group_args)
                    except python_freeipa.exceptions.FreeIPAError as e:
                        if e.message != 'no modifications to be performed':
                            raise
                    print_status("UPDATED", "success")
                    groups_edited += 1
                else:
                    print_status("FAIL", "failure")
                    print(e.message)
                    print(e)
            except Exception as e:
                print_status("FAIL", "failure")
                print(e)

    def chunks(data, n):
        return [data[x : x + n] for x in range(0, len(data), n)]

    def re_auth(instances):
        print("Re-authenticating")
        for ipa in instances:
            ipa.logout()
            ipa.login(config["ipa"]["username"], config["ipa"]["password"])

    def stats():
        print("#######################################################")
        print("")
        print(f"Successfully added {users_added} users.")
        print(f"Successfully edited {users_edited} users.")
        print("")
        print(f"Successfully created {groups_added} groups.")
        print(f"Successfully edited {groups_edited} groups.")
        print("")
        print(
            f"Total FAS groups: {groups_counter}. Total groups added in FreeIPA: { groups_added + groups_edited }"
        )
        print(
            f"Total FAS users: {user_counter}. Total users changed in FreeIPA: { users_added + users_edited }"
        )
        print("")
        print("#######################################################")

    user_counter = 0

    users_added = 0
    users_edited = 0

    alphabet = string.ascii_lowercase + string.digits

    for letter in alphabet:
        search_string = letter + "*"
        groups_to_member_usernames = {}
        groups_to_sponsor_usernames = {}
        print(f"finding users matching {letter}*")
        users = fas.send_request(
            "/user/list", req_params={"search": search_string}, auth=True, timeout=240
        )
        people_count = len(users["people"])
        print(f"{people_count} found")

        for person in progressbar.progressbar(users["people"], redirect_stdout=True):
            user_counter += 1
            if user_counter % config["reauth_every"] == 0:
                re_auth(instances)
            ipa = random.choice(instances)
            print(person["username"], end="    ")
            if person["human_name"]:
                name = person["human_name"].strip()
                name_split = name.split(" ")
                if len(name_split) > 2 or len(name_split) == 1:
                    first_name = "<fnu>"
                    last_name = name
                else:
                    first_name = name_split[0].strip()
                    last_name = name_split[1].strip()
            else:
                name = "<fnu> <lnu>"
                first_name = "<fnu>"
                last_name = "<lnu>"
            try:
                if not only_members:
                    user_args = dict(
                        first_name=first_name,
                        last_name=last_name,
                        full_name=name,
                        gecos=name,
                        display_name=name,
                        home_directory="/home/fedora/%s" % person["username"],
                        disabled=person["status"] != "active",
                        # If they haven't synced yet, they must reset their password:
                        random_pass=True,
                        fasircnick=person["ircnick"].strip() if person["ircnick"] else None,
                        faslocale=person["locale"].strip() if person["locale"] else None,
                        fastimezone=person["timezone"].strip() if person["timezone"] else None,
                        fasgpgkeyid=[person["gpg_keyid"][:16].strip()] if person["gpg_keyid"] else None,
                    )
                    try:
                        ipa.user_add(person["username"], **user_args)
                        print_status("ADDED", "success")
                        users_added += 1
                    except python_freeipa.exceptions.FreeIPAError as e:
                        if (
                            e.message
                            == 'user with name "%s" already exists' % person["username"]
                        ):
                            # Update them instead
                            ipa.user_mod(person["username"], **user_args)
                            print_status("UPDATED", "success")
                            users_edited += 1
                        else:
                            raise e

                for groupname, group in person["group_roles"].items():
                    if groupname in groups_to_member_usernames:
                        groups_to_member_usernames[groupname].append(person["username"])
                    else:
                        groups_to_member_usernames[groupname] = [person["username"]]

                    if group["role_type"] in ["administrator", "sponsor"]:
                        if groupname in groups_to_sponsor_usernames:
                            groups_to_sponsor_usernames[groupname].append(
                                person["username"]
                            )
                        else:
                            groups_to_sponsor_usernames[groupname] = [
                                person["username"]
                            ]

            except python_freeipa.exceptions.Unauthorized as e:
                ipa.login(config["ipa"]["username"], config["ipa"]["password"])
                continue
            except Exception as e:
                print_status("FAIL", "failure")
                print(e)

        group_member_counter = 0
        for group, members in groups_to_member_usernames.items():
            if group in config["ignore_groups"]:
                continue
            with progressbar.ProgressBar(
                max_value=len(members), redirect_stdout=True
            ) as bar:
                bar.max_value = len(members)
                group_member_counter = 0
                for chunk in chunks(members, config["group_chunks"]):
                    group_member_counter += 1
                    try:
                        instances[0].group_add_member(group, chunk, no_members=True)
                        print_status(
                            f"Added members to {group}: {', '.join(chunk)}", "success"
                        )
                    except python_freeipa.exceptions.ValidationError as e:
                        for msg in e.message["member"]["user"]:
                            if msg[1] != "This entry is already a member":
                                print_status(
                                    f"Failed to add {msg[0]} to {group}: {msg[1]}",
                                    "failure",
                                )
                        continue
                    finally:
                        bar.update(group_member_counter * len(chunk))

        group_sponsor_counter = 0
        for group, sponsors in groups_to_sponsor_usernames.items():
            if group in config["ignore_groups"]:
                continue
            with progressbar.ProgressBar(
                max_value=len(sponsors), redirect_stdout=True
            ) as bar:
                group_sponsor_counter = 0
                for chunk in chunks(sponsors, config["group_chunks"]):
                    group_sponsor_counter += 1
                    try:
                        instances[0]._request(
                            "group_add_member_manager", group, {"user": chunk}
                        )
                        print_status(
                            f"Added sponsors to {group}: {', '.join(chunk)}", "success"
                        )
                    except python_freeipa.exceptions.ValidationError as e:
                        for msg in e.message["member"]["user"]:
                            if msg[1] != "This entry is already a member":
                                print_status(
                                    f"Failed to add {msg[0]} as sponsor of {group}: {msg[1]}",
                                    "failure",
                                )
                        continue
                    finally:
                        bar.update(group_sponsor_counter * len(chunk))

    stats()
