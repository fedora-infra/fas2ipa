import random
import string
from collections import defaultdict
from enum import Enum
from urllib.parse import parse_qs, urlencode

import click
import progressbar
import python_freeipa
import vcr
from colorama import Fore, Style
from python_freeipa import ClientLegacy as Client
from fedora.client.fas2 import AccountSystem

from .config import get_config


class FASWrapper:

    _remove_from_request_body = ("_csrf_token", "user_name", "password", "login")

    def __init__(self, config):
        self.fas = AccountSystem(
            config["fas"]["url"],
            username=config["fas"]["username"],
            password=config["fas"]["password"],
        )
        self._replay = config["replay"]
        self._recorder = vcr.VCR(
            ignore_hosts=config["ipa"]["instances"],
            record_mode="new_episodes",
            filter_post_data_parameters=self._remove_from_request_body,
        )
        self._recorder.register_matcher("fas2ipa", self._vcr_match_request)

    def _vcr_match_request(self, r1, r2):
        assert r1.query == r2.query
        body1 = parse_qs(r1.body)
        body2 = parse_qs(r2.body)
        for param in self._remove_from_request_body:
            for body in (body1, body2):
                try:
                    del body[param]
                except KeyError:
                    pass
        assert body1 == body2

    def _vcr_get_cassette_path(self, url, *args, **kwargs):
        params = kwargs.get("req_params", {})
        cassette_path = [
            "fixtures/fas-",
            url[1:].replace("/", "_"),
            ".yaml",
        ]
        if params:
            cassette_path[2:2] = [
                "-",
                urlencode(params, doseq=True),
            ]
        return "".join(cassette_path)

    def send_request(self, url, *args, **kwargs):
        if not self._replay:
            return self.fas.send_request(url, *args, **kwargs)

        cassette_path = self._vcr_get_cassette_path(url, *args, **kwargs)
        with self._recorder.use_cassette(cassette_path, match_on=["fas2ipa"]):
            return self.fas.send_request(url, *args, **kwargs)


class Status(Enum):
    ADDED = "ADDED"
    UPDATED = "UPDATED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


def print_status(status, text=None):
    if status == Status.ADDED:
        color = Style.BRIGHT + Fore.GREEN
    elif status == Status.UPDATED:
        color = Style.BRIGHT + Fore.CYAN
    elif status == Status.FAILED:
        color = Style.BRIGHT + Fore.RED
    elif status == Status.SKIPPED:
        color = Style.BRIGHT + Fore.BLUE
    else:
        raise ValueError(f"Unknown status: {status!r}")
    print(f"{color}{text or status.value}{Style.RESET_ALL}")


def chunks(data, n):
    return [data[x : x + n] for x in range(0, len(data), n)]


def re_auth(config, instances):
    click.echo("Re-authenticating")
    for ipa in instances:
        ipa.logout()
        ipa.login(config["ipa"]["username"], config["ipa"]["password"])


def find_requirements(groups, prereq_id):
    dependent_groups = []
    for group in groups:
        if group["prerequisite_id"] == prereq_id:
            dependent_groups.append(group["name"])
            subdeps = find_requirements(groups, group["id"])
            dependent_groups.extend(subdeps)
    return dependent_groups


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
        status = migrate_group(config, group, ipa)
        print_status(status)
        if status == Status.ADDED:
            added += 1
        elif status == Status.UPDATED:
            edited += 1

    # add groups to agreements
    for agreement in config.get("agreement"):

        toplevel_prereq = fas.send_request(
            "/group/list",
            req_params={"search": agreement["group_prerequisite"]},
            auth=True,
            timeout=240,
        )["groups"][0]["id"]

        agreement_required = find_requirements(fas_groups, toplevel_prereq)

        for dep_name in progressbar.progressbar(
            agreement_required, redirect_stdout=True
        ):
            result = ipa._request(
                "fasagreement_add_group", agreement["name"], {"group": dep_name}
            )
            if result["completed"]:
                print_status(
                    Status.ADDED, f"Added {dep_name} to the {agreement['name']}"
                )
            else:
                error_msg = result["failed"]["member"]["group"][0][1]
                if error_msg == "This entry is already a member":
                    print_status(
                        Status.SKIPPED,
                        f"{dep_name} already requires {agreement['name']}",
                    )
                elif error_msg == "no such entry":
                    print_status(Status.FAILED, f"No group named {dep_name}")
                else:
                    print(result["failed"])

    return dict(groups_added=added, groups_edited=edited, groups_counter=counter,)


def migrate_group(config, group, ipa):
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
    if not users:
        return

    users.sort(key=lambda u: u["username"])

    counter = 0
    added = 0
    edited = 0
    groups_to_member_usernames = defaultdict(list)
    groups_to_sponsor_usernames = defaultdict(list)
    agreements_to_usernames = defaultdict(list)
    max_length = max([len(u["username"]) for u in users])

    for person in progressbar.progressbar(users, redirect_stdout=True):
        counter += 1
        if counter % config["reauth_every"] == 0:
            re_auth(config, instances)
        ipa = random.choice(instances)
        click.echo(person["username"].ljust(max_length + 2), nl=False)
        # Add user
        status = migrate_user(config, person, ipa)
        # Record membership
        for groupname, membership in person["group_roles"].items():
            if groupname in config["ignore_groups"]:
                continue
            groups_to_member_usernames[groupname].append(person["username"])
            if membership["role_type"] in ["administrator", "sponsor"]:
                groups_to_sponsor_usernames[groupname].append(person["username"])
        # Record agreement signatures
        group_names = [g["name"] for g in person["memberships"]]
        for agreement in config.get("agreement"):
            if set(agreement["signed_groups"]) & set(group_names):
                # intersection is not empty: the user signed it
                agreements_to_usernames[agreement["name"]].append(person["username"])
        # Status
        print_status(status)
        if status == Status.ADDED:
            added += 1
        elif status == Status.UPDATED:
            edited += 1

    record_signatures(config, instances, agreements_to_usernames)
    add_users_to_groups(config, instances, groups_to_member_usernames, "members")
    add_users_to_groups(config, instances, groups_to_sponsor_usernames, "sponsors")
    return dict(user_counter=counter, users_added=added, users_edited=edited,)


def migrate_user(config, person, ipa):
    if config["skip_user_add"]:
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
    for groupname, membership in person["group_roles"].items():
        if groupname in config["ignore_groups"]:
            continue
        groups_to_member_usernames[groupname].append(person["username"])
        if membership["role_type"] in ["administrator", "sponsor"]:
            groups_to_sponsor_usernames[groupname].append(person["username"])


def add_users_to_groups(config, instances, groups_to_users, category):
    if config["skip_user_membership"]:
        return

    if category not in ["members", "sponsors"]:
        raise ValueError("title must be eigher member or sponsor")

    click.echo(f"Adding {category} to groups")
    total = sum([len(members) for members in groups_to_users.values()])
    if total == 0:
        click.echo("Nothing to do.")
        return
    counter = 0
    with progressbar.ProgressBar(max_value=total, redirect_stdout=True) as bar:
        for group in sorted(groups_to_users):
            members = groups_to_users[group]
            for chunk in chunks(members, config["group_chunks"]):
                counter += len(chunk)
                if counter % config["reauth_every"] == 0:
                    re_auth(config, instances)
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
                except python_freeipa.exceptions.NotFound as e:
                    print_status(
                        Status.FAILED,
                        f"Failed to add {chunk} in the {category} of {group}: {e}",
                    )
                finally:
                    bar.update(counter)


def record_signatures(config, instances, agreements_to_usernames):
    if config["skip_user_signature"]:
        return

    for agreement in config.get("agreement"):
        click.echo(f"Recording signers of the {agreement['name']} agreement")
        signers = agreements_to_usernames.get(agreement["name"], [])
        if not signers:
            click.echo("Nothing to do.")
            continue
        counter = 0
        with progressbar.ProgressBar(
            max_value=len(signers), redirect_stdout=True
        ) as bar:
            for chunk in chunks(signers, config["group_chunks"]):
                counter += len(chunk)
                if counter % config["reauth_every"] == 0:
                    re_auth(config, instances)
                ipa = random.choice(instances)
                response = ipa._request(
                    "fasagreement_add_user", agreement["name"], {"user": chunk},
                )
                for msg in response["failed"]["memberuser"]["user"]:
                    if msg[1] != "This entry is already a member":
                        print_status(
                            Status.FAILED,
                            f"Could not mark {msg[0]} as having signed "
                            f"{agreement['name']}: {response['failed']}",
                        )
                bar.update(counter)


def create_agreements(config, ipa):
    click.echo("Creating Agreements")
    for agreement in config.get("agreement"):
        with open(agreement["description_file"], "r") as f:
            agreement_description = f.read()
        try:
            ipa._request(
                "fasagreement_add",
                agreement["name"],
                {"description": agreement_description},
            )
        except python_freeipa.exceptions.DuplicateEntry as e:
            print_status(Status.SKIPPED, str(e))


class Stats(defaultdict):
    def __init__(self, *args, **kwargs):
        super().__init__(lambda: 0, *args, **kwargs)

    def update(self, new):
        """Adds to the existing stats instead of overwriting"""
        if new is None:
            return
        for key, value in new.items():
            if not isinstance(value, int):
                raise ValueError("Only integers are allowed in stats dicts")
            self[key] += value

    def print(self):
        groups_changed = self["groups_added"] + self["groups_edited"]
        users_changed = self["users_added"] + self["users_edited"]
        print(
            f"""~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Successfully added {self['users_added']} users.
Successfully edited {self['users_edited']} users.

Successfully created {self['groups_added']} groups.
Successfully edited {self['groups_edited']} groups.

Total FAS groups: {self['groups_counter']}. Total groups changed in FreeIPA: { groups_changed }
Total FAS users: {self['user_counter']}. Total users changed in FreeIPA: { users_changed }

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
"""
        )


@click.command()
@click.option("--skip-groups", is_flag=True, help="Skip group creation")
@click.option(
    "--skip-user-add", is_flag=True, help="Don't add or update users",
)
@click.option(
    "--skip-user-membership", is_flag=True, help="Don't add users to groups",
)
@click.option(
    "--skip-user-signature",
    is_flag=True,
    help="Don't store users signatures of agreements",
)
@click.option("--users-start-at", help="Start migrating users at that letter")
def cli(
    skip_groups,
    skip_user_add,
    skip_user_membership,
    skip_user_signature,
    users_start_at,
):
    config = get_config()
    config["skip_groups"] = skip_groups
    config["skip_user_add"] = skip_user_add
    config["skip_user_membership"] = skip_user_membership
    config["skip_user_signature"] = skip_user_signature

    fas = FASWrapper(config)
    click.echo("Logged into FAS")

    instances = []
    for instance in config["ipa"]["instances"]:
        ipa = Client(host=instance, verify_ssl=config["ipa"]["cert_path"])
        ipa.login(config["ipa"]["username"], config["ipa"]["password"])
        instances.append(ipa)
    click.echo("Logged into IPA")

    stats = Stats()

    if config.get("agreement"):
        create_agreements(config, ipa)

    groups_stats = migrate_groups(config, fas, ipa)
    stats.update(groups_stats)

    alphabet = list(string.ascii_lowercase)
    if users_start_at:
        start_index = alphabet.index(users_start_at.lower())
        del alphabet[:start_index]

    for letter in alphabet:
        click.echo(f"finding users starting with {letter}")
        users = fas.send_request(
            "/user/list", req_params={"search": letter + "*"}, auth=True, timeout=240
        )
        users_stats = migrate_users(config, users["people"], instances)
        stats.update(users_stats)

    stats.print()
