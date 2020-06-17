import random
import string
from collections import defaultdict
from enum import Enum

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


class Status(Enum):
    ADDED = "ADDED"
    UPDATED = "UPDATED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


def print_status(status, text=None):
    if status in [Status.ADDED, Status.UPDATED]:
        color = Fore.GREEN
    elif status == Status.FAILED:
        color = Fore.RED
    elif status == Status.SKIPPED:
        color = Fore.BLACK
    else:
        raise ValueError(f"Unknown status: {status!r}")
    print(f"{Style.BRIGHT}{color}{text or status.value}{Style.RESET_ALL}")


def chunks(data, n):
    return [data[x : x + n] for x in range(0, len(data), n)]


def re_auth(config, instances):
    click.echo("Re-authenticating")
    for ipa in instances:
        ipa.logout()
        ipa.login(config["ipa"]["username"], config["ipa"]["password"])


def print_stats(stats):
    groups_changed = stats["groups_added"] + stats["groups_edited"]
    users_changed = stats["users_added"] + stats["users_edited"]
    print(
        f"""#######################################################

Successfully added {stats['users_added']} users.
Successfully edited {stats['users_edited']} users.

Successfully created {stats['groups_added']} groups.
Successfully edited {stats['groups_edited']} groups.

Total FAS groups: {stats['groups_counter']}. Total groups changed in FreeIPA: { groups_changed }
Total FAS users: {stats['user_counter']}. Total users changed in FreeIPA: { users_changed }

#######################################################
"""
    )


def migrate_groups(config, fas, ipa):
    if config["skip_groups"]:
        return {}

    added = 0
    edited = 0
    counter = 0

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
    fas_groups.sort(key=lambda g: g["name"])
    click.echo(f"Got {len(fas_groups)} groups!")

    max_length = max([len(g["name"]) for g in fas_groups])
    for group in progressbar.progressbar(fas_groups, redirect_stdout=True):
        counter += 1
        click.echo(group["name"].ljust(max_length + 2), nl=False)
        status = migrate_group(group, ipa)
        print_status(status)
        if status == Status.ADDED:
            added += 1
        elif status == Status.UPDATED:
            edited += 1

    return dict(groups_added=added, groups_edited=edited, groups_counter=counter,)


def migrate_group(group, ipa):
    name = group["name"].lower()
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
        return Status.ADDED
    except python_freeipa.exceptions.FreeIPAError as e:
        if e.message == 'group with name "%s" already exists' % name:
            try:
                ipa.group_mod(name, **group_args)
            except python_freeipa.exceptions.FreeIPAError as e:
                if e.message != "no modifications to be performed":
                    raise
            return Status.UPDATED
        else:
            print(e.message)
            print(e)
            print(url, mailing_list, irc_string)
            return Status.FAILED
    except Exception as e:
        print(e)
        print(url, mailing_list, irc_string)
        return Status.FAILED


def migrate_users(config, users, instances):
    print(f"{len(users)} found")
    users.sort(key=lambda u: u["username"])

    counter = 0
    added = 0
    edited = 0
    groups_to_member_usernames = {}
    groups_to_sponsor_usernames = {}
    max_length = max([len(u["username"]) for u in users])

    for person in progressbar.progressbar(users, redirect_stdout=True):
        counter += 1
        if counter % config["reauth_every"] == 0:
            re_auth(config, instances)
        ipa = random.choice(instances)
        click.echo(person["username"].ljust(max_length + 2), nl=False)
        status = migrate_user(config, person, ipa)
        record_membership(
            config, person, groups_to_member_usernames, groups_to_sponsor_usernames
        )
        print_status(status)
        if status == Status.ADDED:
            added += 1
        elif status == Status.UPDATED:
            edited += 1

    add_users_to_groups(config, instances, groups_to_member_usernames, "members")
    add_users_to_groups(config, instances, groups_to_sponsor_usernames, "sponsors")
    return dict(user_counter=counter, users_added=added, users_edited=edited,)


def migrate_user(config, person, ipa):
    if config["only_members"]:
        return Status.SKIPPED
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
            fasgpgkeyid=[person["gpg_keyid"][:16].strip()]
            if person["gpg_keyid"]
            else None,
        )
        try:
            ipa.user_add(person["username"], **user_args)
            return Status.ADDED
        except python_freeipa.exceptions.FreeIPAError as e:
            if e.message == 'user with name "%s" already exists' % person["username"]:
                # Update them instead
                ipa.user_mod(person["username"], **user_args)
                return Status.UPDATED
            else:
                raise e
    except python_freeipa.exceptions.Unauthorized:
        ipa.login(config["ipa"]["username"], config["ipa"]["password"])
        return migrate_user(config, person, ipa)
    except Exception as e:
        print(e)
        return Status.FAILED


def record_membership(
    config, person, groups_to_member_usernames, groups_to_sponsor_usernames
):
    for groupname, group in person["group_roles"].items():
        if groupname in config["ignore_groups"]:
            continue
        if groupname in groups_to_member_usernames:
            groups_to_member_usernames[groupname].append(person["username"])
        else:
            groups_to_member_usernames[groupname] = [person["username"]]
        if group["role_type"] in ["administrator", "sponsor"]:
            if groupname in groups_to_sponsor_usernames:
                groups_to_sponsor_usernames[groupname].append(person["username"])
            else:
                groups_to_sponsor_usernames[groupname] = [person["username"]]


def add_users_to_groups(config, instances, groups_to_users, category):
    if category not in ["members", "sponsors"]:
        raise ValueError("title must be eigher member or sponsor")

    click.echo(f"Adding {category} to groups")
    total = sum([len(members) for members in groups_to_users.values()])
    if total == 0:
        click.echo("Nothing to do.")
        return
    counter = 0
    with progressbar.ProgressBar(max_value=total, redirect_stdout=True) as bar:
        for group, members in groups_to_users.items():
            for chunk in chunks(members, config["group_chunks"]):
                counter += len(chunk)
                ipa = random.choice(instances)
                try:
                    if category == "members":
                        ipa.group_add_member(group, chunk, no_members=True)
                    elif category == "sponsors":
                        ipa._request("group_add_member_manager", group, {"user": chunk})
                    print_status(
                        Status.ADDED, f"Added {category} to {group}: {', '.join(chunk)}"
                    )
                except python_freeipa.exceptions.ValidationError as e:
                    for msg in e.message["member"]["user"]:
                        if msg[1] != "This entry is already a member":
                            print_status(
                                Status.FAILED,
                                f"Failed to add {msg[0]} in the {category} of {group}: {msg[1]}",
                            )
                    continue
                finally:
                    bar.update(counter)


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
    config["skip_groups"] = skip_groups
    config["only_members"] = only_members

    fas = FASWrapper(config)
    click.echo("Logged into FAS")

    instances = []
    for instance in config["ipa"]["instances"]:
        ipa = Client(host=instance, verify_ssl=config["ipa"]["cert_path"])
        ipa.login(config["ipa"]["username"], config["ipa"]["password"])
        instances.append(ipa)
    click.echo("Logged into IPA")

    stats = defaultdict(lambda: 0)

    updated_stats = migrate_groups(config, fas, ipa)
    stats.update(updated_stats)

    alphabet = string.ascii_lowercase + string.digits

    for letter in alphabet:
        click.echo(f"finding users starting with {letter}")
        users = fas.send_request(
            "/user/list", req_params={"search": letter + "*"}, auth=True, timeout=240
        )
        updated_stats = migrate_users(config, users["people"], instances)
        stats.update(updated_stats)

    print_stats(stats)
