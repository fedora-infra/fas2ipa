import string
import re
from collections import defaultdict

import click
import progressbar
import python_freeipa

from .status import Status, print_status
from .utils import ObjectManager
from .statistics import Stats


CREATION_TIME_RE = re.compile(r"([0-9 :-]+).[0-9]+\+00:00")


class Users(ObjectManager):
    def __init__(self, *args, agreements, **kwargs):
        super().__init__(*args, **kwargs)
        self.agreements = agreements

    def migrate_users(self, users_start_at=None, restrict_users=()):
        if restrict_users:
            user_patterns = [
                pattern
                for pattern in restrict_users
                if not users_start_at
                or pattern.replace("*", "\u0010ffff") >= users_start_at
            ]
        else:
            alphabet = list(string.ascii_lowercase)
            if users_start_at:
                start_index = alphabet.index(users_start_at[0].lower())
                user_patterns = [
                    pattern + "*"
                    for pattern in [users_start_at] + alphabet[start_index + 1 :]
                ]
            else:
                user_patterns = [pattern + "*" for pattern in alphabet]

        stats = Stats()

        for pattern in user_patterns:
            if "*" in pattern:
                click.echo(f"finding users matching {pattern!r}")
            else:
                click.echo(f"finding user {pattern!r}")
            result = self.fas.send_request(
                "/user/list", req_params={"search": pattern}, auth=True, timeout=240,
            )
            if users_start_at:
                users_per_pattern = [
                    u for u in result["people"] if u.username >= users_start_at
                ]
            else:
                users_per_pattern = result["people"]
            users_stats = self._migrate_users(users_per_pattern)
            stats.update(users_stats)

        return stats

    def _migrate_users(self, users):
        print(f"{len(users)} found")
        if not users:
            return

        users.sort(key=lambda u: u["username"])

        counter = 0
        added = 0
        edited = 0
        skipped = 0
        groups_to_member_usernames = defaultdict(list)
        groups_to_unapproved_member_usernames = defaultdict(list)
        groups_to_sponsor_usernames = defaultdict(list)
        agreements_to_usernames = defaultdict(list)
        max_length = max([len(u["username"]) for u in users])

        for person in progressbar.progressbar(users, redirect_stdout=True):
            counter += 1
            self.check_reauth(counter)
            click.echo(person["username"].ljust(max_length + 2), nl=False)
            # Add user
            status = self.migrate_user(person)
            if status != Status.SKIPPED:
                # Record membership
                for groupname, membership in person["group_roles"].items():
                    if groupname in self.config["groups"]["ignore"]:
                        continue
                    if membership["role_status"] == "approved":
                        groups_to_member_usernames[groupname].append(person["username"])
                        if membership["role_type"] in ["administrator", "sponsor"]:
                            groups_to_sponsor_usernames[groupname].append(
                                person["username"]
                            )
                    else:
                        groups_to_unapproved_member_usernames[groupname].append(
                            person["username"]
                        )
                # Record agreement signatures
                group_names = [g["name"] for g in person["memberships"]]
                for agreement in self.config.get("agreement"):
                    if set(agreement["signed_groups"]) & set(group_names):
                        # intersection is not empty: the user signed it
                        agreements_to_usernames[agreement["name"]].append(
                            person["username"]
                        )

            # Status
            print_status(status)
            if status == Status.ADDED:
                added += 1
            elif status == Status.UPDATED:
                edited += 1
            elif status == Status.SKIPPED:
                skipped += 1

        self.agreements.record_user_signatures(agreements_to_usernames)
        self.add_users_to_groups(groups_to_member_usernames, "members")
        self.add_users_to_groups(groups_to_sponsor_usernames, "sponsors")
        self.remove_users_from_groups(groups_to_unapproved_member_usernames)
        return {
            "user_counter": counter,
            "users_added": added,
            "users_edited": edited,
            "users_skipped": skipped,
        }

    @classmethod
    def _compact_dict(cls, val):
        # If it has ID fields, it's just to bulky and uninformative.
        if any("id" in key for key in val):
            return "{…}"

        items_strs = (f"'{k}': …" for k in val.keys())
        return f"{{{', '.join(items_strs)}}}"

    @classmethod
    def _compact_sequence(cls, val):
        return (cls._compact_value(item) for item in val)

    @classmethod
    def _compact_list(cls, val):
        return list(cls._compact_sequence(val))

    @classmethod
    def _compact_tuple(cls, val):
        return tuple(cls._compact_sequence(val))

    @classmethod
    def _compact_set(cls, val):
        return set(cls._compact_sequence(val))

    @classmethod
    def _compact_value(cls, val):
        if isinstance(val, dict):
            return cls._compact_dict(val)
        elif isinstance(val, list):
            return cls._compact_list(val)
        elif isinstance(val, tuple):
            return cls._compact_tuple(val)
        elif isinstance(val, set):
            return cls._compact_set(val)
        else:
            return val

    def migrate_user(self, person):
        if self.config["skip_user_add"]:
            return Status.SKIPPED
        if self.config["users"]["skip_spam"] and person["status"] == "spamcheck_denied":
            return Status.SKIPPED

        # Don't modify the original object, and remove all key/value pairs that should
        # be ignored
        person = {
            key: value
            for key, value in person.items()
            if not (
                key in {"group_roles", "security_answer", "security_question"}
                or "token" in key
            )
        }

        # Pop all key/value pairs that are processed
        username = person.pop("username")
        human_name = person.pop("human_name")
        status = person.pop("status")
        ircnick = person.pop("ircnick")
        locale = person.pop("locale")
        timezone = person.pop("timezone")
        gpg_keyid = person.pop("gpg_keyid")
        creation = person.pop("creation")

        # Fail if any details are left, i.e. unprocessed
        if person:
            print("Unprocessed details:")
            for key, value in sorted(person.items(), key=lambda x: x[0]):
                if (
                    key in {"email", "ssh_key", "telephone", "facsimile"}
                    or "password" in key
                ):
                    print(f"\t{key}: <…shhhhh…>")
                else:
                    print(f"\t{key}: {self._compact_value(value)}")
            return Status.FAILED

        if human_name:
            name = human_name.strip()
            name_split = name.split(" ")
            if len(name_split) > 2 or len(name_split) == 1:
                first_name = "<first-name-unset>"
                last_name = name
            else:
                first_name = name_split[0].strip()
                last_name = name_split[1].strip()
        else:
            name = "<first-name-unset> <last-name-unset>"
            first_name = "<first-name-unset>"
            last_name = "<last-name-unset>"
        try:
            user_args = {
                "first_name": first_name,
                "last_name": last_name,
                "full_name": name,
                "gecos": name,
                "display_name": name,
                "home_directory": f"/home/fedora/{username}",
                "disabled": status != "active",
                "fasircnick": ircnick.strip() if ircnick else None,
                "faslocale": locale.strip() if locale else None,
                "fastimezone": timezone.strip() if timezone else None,
                "fasgpgkeyid": [gpg_keyid[:16].strip()] if gpg_keyid else None,
                "fasstatusnote": status.strip(),
                "fascreationtime": CREATION_TIME_RE.sub(r"\1Z", creation),
            }
            try:
                user_add_args = user_args.copy()
                # If they haven't synced yet, they must reset their password:
                user_add_args["random_pass"] = True
                self.ipa.user_add(username, **user_add_args)
                return Status.ADDED
            except python_freeipa.exceptions.FreeIPAError as e:
                if e.message == f'user with name "{username}" already exists':
                    # Update them instead
                    self.ipa.user_mod(username, **user_args)
                    return Status.UPDATED
                else:
                    raise

        except python_freeipa.exceptions.Unauthorized:
            self.ipa.login(
                self.config["ipa"]["username"], self.config["ipa"]["password"]
            )
            return self.migrate_user(person)
        except python_freeipa.exceptions.FreeIPAError as e:
            if e.message != "no modifications to be performed":
                print(e)
                return Status.FAILED
            return Status.UNMODIFIED
        except Exception as e:
            print(e)
            return Status.FAILED

    def add_users_to_groups(self, groups_to_users, category):
        if self.config["skip_user_membership"]:
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
                for chunk in self.chunks(members):
                    counter += len(chunk)
                    self.check_reauth(counter)
                    added = set(chunk[:])
                    try:
                        if category == "members":
                            self.ipa.group_add_member(
                                self.config["groups"]["prefix"] + group,
                                chunk,
                                no_members=True,
                            )
                        elif category == "sponsors":
                            result = self.ipa._request(
                                "group_add_member_manager",
                                self.config["groups"]["prefix"] + group,
                                {"user": chunk},
                            )
                            if result["failed"]["membermanager"]["user"]:
                                raise python_freeipa.exceptions.ValidationError(
                                    result["failed"]
                                )
                    except python_freeipa.exceptions.ValidationError as e:
                        errors = []
                        for member_type in ("member", "membermanager"):
                            try:
                                errors.extend(e.message[member_type]["user"])
                            except KeyError:
                                continue
                        for msg in errors:
                            if msg[1] == "This entry is already a member":
                                added.remove(msg[0])
                            else:
                                print_status(
                                    Status.FAILED,
                                    f"Failed to add {msg[0]} in the {category} of {group}: "
                                    + msg[1],
                                )
                    except python_freeipa.exceptions.NotFound as e:
                        print_status(
                            Status.FAILED,
                            f"Failed to add {chunk} in the {category} of {group}: {e}",
                        )
                    except Exception as e:
                        print_status(
                            Status.FAILED,
                            f"Failed to add {chunk} in the {category} of {group}: {e}",
                        )
                    else:
                        if added:
                            print_status(
                                Status.ADDED,
                                f"Added {category} to {group}: {', '.join(sorted(added))}",
                            )
                    finally:
                        bar.update(counter)

    def remove_users_from_groups(self, groups_to_users):
        if self.config["skip_user_membership"]:
            return

        click.echo("Removing unapproved users from groups")
        total = sum([len(members) for members in groups_to_users.values()])
        if total == 0:
            click.echo("Nothing to do.")
            return
        counter = 0
        with progressbar.ProgressBar(max_value=total, redirect_stdout=True) as bar:
            for group in sorted(groups_to_users):
                members = groups_to_users[group]
                for chunk in self.chunks(members):
                    counter += len(chunk)
                    self.check_reauth(counter)
                    removed = set(chunk[:])
                    try:
                        self.ipa.group_remove_member(
                            self.config["groups"]["prefix"] + group,
                            chunk,
                            no_members=True,
                        )
                    except python_freeipa.exceptions.ValidationError as e:
                        errors = []
                        for member_type in ("member", "membermanager"):
                            try:
                                errors.extend(e.message[member_type]["user"])
                            except KeyError:
                                continue
                        for msg in errors:
                            if msg[1] == "This entry is not a member":
                                removed.remove(msg[0])
                            else:
                                print_status(
                                    Status.FAILED,
                                    f"Failed to remove {msg[0]} from {group}: "
                                    + msg[1],
                                )
                    except python_freeipa.exceptions.NotFound as e:
                        print_status(
                            Status.FAILED,
                            f"Failed to remove {chunk} from {group}: {e}",
                        )
                    except Exception as e:
                        print_status(
                            Status.FAILED,
                            f"Failed to remove {chunk} from {group}: {e}",
                        )
                    else:
                        if removed:
                            print_status(
                                Status.REMOVED,
                                f"Removed from {group}: {', '.join(sorted(removed))}",
                            )
                    finally:
                        bar.update(counter)
