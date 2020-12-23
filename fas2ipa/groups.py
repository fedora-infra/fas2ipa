import click
import progressbar
import python_freeipa
from collections import defaultdict
from typing import Any, Dict, List

from .status import Status, print_status
from .utils import ObjectManager


class Groups(ObjectManager):
    def __init__(self, *args, agreements, **kwargs):
        super().__init__(*args, **kwargs)
        self.agreements = agreements

    def pull_from_fas(self) -> Dict[str, List[Dict]]:
        fas_groups = {}

        for fas_name, fas_inst in self.fas_instances.items():
            click.echo(f"Pulling group information from FAS ({fas_name})...")

            fas_conf = self.config["fas"][fas_name]
            groups = fas_inst.send_request(
                "/group/list",
                req_params={"search": fas_conf["groups"]["search"]},
                auth=True,
                timeout=240,
            )["groups"]
            groups.sort(key=lambda g: g["name"])
            click.echo(f"Got {len(groups)} groups!")
            fas_groups[fas_name] = groups

        return fas_groups

    def push_to_ipa(
        self,
        groups: Dict[str, List[Dict]],
        conflicts: Dict[str, List[Dict[str, Any]]],
    ) -> dict:
        added = 0
        edited = 0
        counter = 0

        if not conflicts:
            conflicts = {}
        skip_conflicts = set(self.config["groups"].get("skip_conflicts", ()))

        for fas_name, fas_groups in groups.items():
            click.echo(f"Pushing {fas_name} group information to IPA...")

            fas_conf = self.config["fas"][fas_name]

            # Start by creating groups
            fas_groups = [
                g for g in fas_groups
                if g["name"] not in fas_conf["groups"].get("ignore", ())
            ]

            name_max_length = max([len(g["name"]) for g in fas_groups])

            for group in progressbar.progressbar(fas_groups, redirect_stdout=True):
                counter += 1

                group_conflicts = set(conflicts.get(group["name"], ()))
                group_skip_conflicts = skip_conflicts & group_conflicts
                if group_skip_conflicts:
                    print_status(
                        Status.FAILED,
                        f"[{fas_name}: Skipping group '{group['name']}' because of conflicts:"
                        f" {', '.join(group_skip_conflicts)}",
                    )
                    continue

                self.check_reauth(counter)
                click.echo(group["name"].ljust(name_max_length + 2), nl=False)
                status = self._write_group_to_ipa(fas_name, group)
                print_status(status)
                if status == Status.ADDED:
                    added += 1
                elif status == Status.UPDATED:
                    edited += 1

            click.echo(f"Done with {fas_name}")

        # add groups to agreements
        click.echo("Recording group requirements in IPA...")
        self.agreements.record_group_requirements(groups)

        click.echo("Done.")

        return dict(groups_added=added, groups_edited=edited, groups_counter=counter,)

    def _write_group_to_ipa(self, fas_name: str, group: dict):
        name = self.config["fas"][fas_name]["groups"].get("prefix", "") + group["name"].lower()
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

    def find_group_conflicts(self, fas_groups: Dict[str, List[Dict]]) -> Dict[str, List[str]]:
        """Compare groups from different FAS instances and flag conflicts."""
        click.echo("Checking for conflicts between groups from different FAS instances")

        groups_to_conflicts = {}

        groupnames_to_fas = defaultdict(set)

        for fas_name, group_objs in fas_groups.items():
            for group_obj in group_objs:
                groupnames_to_fas[group_obj["name"]].add(fas_name)

        for group_name, fas_names in sorted(groupnames_to_fas.items(), key=lambda x: x[0]):
            if len(fas_names) == 1:
                continue

            groups_to_conflicts[group_name] = group_conflicts = defaultdict(list)

            group_conflicts["same_group_name"] = {"fas_names": fas_names}

        click.echo("Done checking group conflicts.")
        click.echo(f"Found {len(groups_to_conflicts)} groups with conflicts.")

        return groups_to_conflicts
