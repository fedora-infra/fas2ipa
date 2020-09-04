from collections import defaultdict


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
Skipped {self['users_skipped']} users.

Successfully created {self['groups_added']} groups.
Successfully edited {self['groups_edited']} groups.

Total FAS groups: {self['groups_counter']}. Total groups changed in FreeIPA: { groups_changed }
Total FAS users: {self['user_counter']}. Total users changed in FreeIPA: { users_changed }

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
"""
        )
