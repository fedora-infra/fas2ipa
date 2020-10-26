import click
import progressbar
import python_freeipa

from .status import Status, print_status
from .utils import ObjectManager


class Groups(ObjectManager):
    def __init__(self, *args, agreements, **kwargs):
        super().__init__(*args, **kwargs)
        self.agreements = agreements

    def migrate_groups(self):
        if self.config["skip_groups"]:
            return {}

        added = 0
        edited = 0
        counter = 0

        # Start by creating groups
        click.echo("Getting the list of groups...")
        fas_groups = self.fas.send_request(
            "/group/list",
            req_params={"search": self.config["groups"]["search"]},
            auth=True,
            timeout=240,
        )
        fas_groups = [
            g for g in fas_groups["groups"]
            if g["name"] not in self.config["groups"]["ignore"]
        ]
        fas_groups.sort(key=lambda g: g["name"])
        click.echo(f"Got {len(fas_groups)} groups!")

        max_length = max([len(g["name"]) for g in fas_groups])

        for group in progressbar.progressbar(fas_groups, redirect_stdout=True):
            counter += 1
            self.check_reauth(counter)
            click.echo(group["name"].ljust(max_length + 2), nl=False)
            status = self.migrate_group(group)
            print_status(status)
            if status == Status.ADDED:
                added += 1
            elif status == Status.UPDATED:
                edited += 1

        # add groups to agreements
        self.agreements.record_group_requirements(fas_groups)

        return dict(groups_added=added, groups_edited=edited, groups_counter=counter,)

    def migrate_group(self, group):
        name = self.config["groups"]["prefix"] + group["name"].lower()
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
            self.ipa.group_add(name, **group_args)
            return Status.ADDED
        except python_freeipa.exceptions.FreeIPAError as e:
            if e.message == 'group with name "%s" already exists' % name:
                try:
                    self.ipa.group_mod(name, **group_args)
                except python_freeipa.exceptions.FreeIPAError as e:
                    if e.message != "no modifications to be performed":
                        raise
                return Status.UNMODIFIED
            else:
                print(e.message)
                print(e)
                print(url, mailing_list, irc_string)
                return Status.FAILED
        except Exception as e:
            print(e)
            print(url, mailing_list, irc_string)
            return Status.FAILED
